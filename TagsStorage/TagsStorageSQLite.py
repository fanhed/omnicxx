#!/usr/bin/env python
# -*- encoding:utf-8 -*-


from ITagsStorage import ITagsStorage
from TagEntry import TagEntry
from FileEntry import FileEntry
from Misc import ToU

import os, os.path
import tempfile
import time
import subprocess
import platform
import sqlite3

STORAGE_VERSION = 3000

# 这两个变量暂时只对本模块生效
# FIXME: 应该使用公共的模块定义这两个变量
CPP_SOURCE_EXT = set(['c', 'cpp', 'cxx', 'c++', 'cc'])
CPP_HEADER_EXT = set(['h', 'hpp', 'hxx', 'hh', 'inl', 'inc'])

def Escape(string, chars):
    result = ''
    for char in string:
        if char in chars:
            # 转义之
            result += '\\' + char
        else:
            result += char
    return result

def MakeQMarkString(count):
    '''生成用于 sqlite3 语句的问号占位符，是包括括号的，一般用 IN 语法
    count 表示需要的 ? 的数量'''
    if count <= 1:
        return "(?)"
    else:
        return "(%s)" % ", ".join(["?" for i in range(count)])

class PrintExcept(*args):
    '''打印异常'''
    pass

class TagsStorageSQLite(ITagsStorage):
    def __init__(self):
        ITagsStorage.__init__(self)
        self.fname = ''     # 数据库文件, os.path.realpath() 的返回值
        self.db = None      # sqlite3 的连接实例, 取此名字是为了与 codelite 统一

    def __del__(self):
        if self.db:
            self.db.close()
            self.db = None

    def GetVersion(self):
        global STORAGE_VERSION
        return STORAGE_VERSION

    def GetTagsBySQL(self, sql):
        '''外部/调试接口，返回元素为字典的列表'''
        if not sql:
            return []
        tags = self.DoFetchTags(sql)
        return [tag.ToDict() for tag in tags]

    def Begin(self):
        if self.db:
            try:
                self.db.execute("begin;")
            except sqlite3.OperationalError:
                PrintExcept()

    def Commit(self):
        if self.db:
            try:
                self.db.commit()
            except sqlite3.OperationalError:
                PrintExcept()

    def Rollback(self):
        if self.db:
            try:
                self.db.rollback()
            except sqlite3.OperationalError:
                PrintExcept()

    def CloseDatabase(self):
        if self.IsOpen():
            self.db.close()
            self.db = None

    def OpenDatabase(self, fname):
        '''正常返回0, 异常返回-1'''
        # TODO: 验证文件是否有效

        # 如果相同, 表示已经打开了相同的数据库, 直接返回
        if self.IsOpen() and self.fname == os.path.realpath(fname):
            return 0

        # Did we get a file name to use?
        # 未打开任何数据库, 且请求打开的文件无效, 直接返回
        if not self.IsOpen() and not fname:
            return -1

        # We did not get any file name to use BUT we
        # do have an open database, so we will use it
        # 传进来的是无效的文件, 但已经打开了某个数据库, 继续用之
        if not fname:
            return 0

        orig_fname = fname
        if not fname == ':memory:' # ':memory:' 是一个特殊值, 表示内存数据库
            fname = os.path.realpath(fname)

        # 先把旧的关掉
        self.CloseDatabase()

        try:
            self.db = sqlite3.connect(ToU(fname))
            self.db.text_factory = str # 以字符串方式保存而不是 unicode
            self.CreateSchema()
            self.fname = fname
            return 0
        except sqlite3.OperationalError:
            PrintExcept()
            return -1

    def ExecuteSQL(self, sql):
        '''NOTE: 不完全封装, 暂时不支持如果封装带占位符形式的参数, 懒得测试'''
        if not sql or not self.IsOpen():
            return -1
        try:
            self.db.execute(sql)
        except sqlite3.OperationalError:
            PrintExcept()
            return -1
        return 0

    def ExecuteSQLScript(self, sql):
        if not sql or not self.IsOpen():
            return -1
        try:
            self.db.executescript(sql)
        except sqlite3.OperationalError:
            PrintExcept()
            return -1
        return 0

    def DropSchema(self):
        # TODO: 需要识别版本
        version = self.GetSchemaVersion()
        sqls = [
            # and drop tables
            "DROP TABLE IF EXISTS TAGS;",
            "DROP TABLE IF EXISTS FILES;",
            "DROP TABLE IF EXISTS TAGS_VERSION;",

            # drop indexes
            "DROP INDEX IF EXISTS FILES_UNIQ_IDX;",
            "DROP INDEX IF EXISTS TAGS_UNIQ_IDX;",
            "DROP INDEX IF EXISTS TAGS_KIND_IDX;",
            "DROP INDEX IF EXISTS TAGS_FILE_IDX;",
            "DROP INDEX IF EXISTS TAGS_NAME_IDX;",
            "DROP INDEX IF EXISTS TAGS_SCOPE_IDX;",
            "DROP INDEX IF EXISTS TAGS_VERSION_UNIQ_IDX;",
        ]

        for sql in sqls:
            self.ExecuteSQL(sql)

    def CreateSchema(self):
        try:
            # improve performace by using pragma command:
            # (this needs to be done before the creation of the
            # tables and indices)
            sql = "PRAGMA synchronous = OFF;"
            self.ExecuteSQL(sql)

            sql = "PRAGMA temp_store = MEMORY;"
            self.ExecuteSQL(sql)

            # TAGS 表
            sql = '''
            CREATE TABLE IF NOT EXISTS TAGS (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            STRING,
                file            STRING,
                fileid          INTEGER,
                line            INTEGER,
                kind            STRING,
                scope           STRING,
                parent_kind     STRING,
                access          STRING,
                inherits        STRING,
                signature       STRING,
                extra           STRING);
            '''

            self.ExecuteSQL(sql)

            # FILES 表
            sql = '''
            CREATE TABLE IF NOT EXISTS FILES (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                file    STRING,
                tagtime INTEGER);
            '''
            self.ExecuteSQL(sql)

            sqls = [
                'CREATE UNIQUE INDEX IF NOT EXISTS FILES_UNIQ_IDX ON FILES(file);';

                # 唯一索引 mod on 2011-01-07
                # 不同源文件文件之间会存在相同的符号
                # 最靠谱是(name, file, line, kind, scope, signature)
                # 但是可能会比较慢, 所以尽量精简
                # 假定同一行不会存在相同名字和类型的符号
                '''
                CREATE UNIQUE INDEX IF NOT EXISTS TAGS_UNIQ_IDX ON TAGS(
                    name, file, kind, scope, signature);
                ''',

                "CREATE INDEX IF NOT EXISTS TAGS_KIND_IDX ON TAGS(kind);",
                "CREATE INDEX IF NOT EXISTS TAGS_FILE_IDX ON TAGS(file);",
                "CREATE INDEX IF NOT EXISTS TAGS_NAME_IDX ON TAGS(name);",
                "CREATE INDEX IF NOT EXISTS TAGS_SCOPE_IDX ON TAGS(scope);",
                #"CREATE INDEX IF NOT EXISTS TAGS_PARENT_IDX ON TAGS(parent);",

                # TAGS_VERSION 表
                "CREATE TABLE IF NOT EXISTS TAGS_VERSION (version INTEGER PRIMARY KEY);",
                "CREATE UNIQUE INDEX IF NOT EXISTS TAGS_VERSION_UNIQ_IDX ON TAGS_VERSION(version);",
            ]

            for sql in sqls:
                self.ExecuteSQL(sql)

            # 插入数据
            self.db.execute("INSERT OR REPLACE INTO TAGS_VERSION VALUES(?)",
                            (self.GetVersion(), ))

            # 必须提交
            self.Commit()
        except sqlite3.OperationalError:
            PrintExcept()

    def RecreateDatabase(self):
        '''只有打开数据库的时候才能进行这个操作'''
        if not self.IsOpen():
            return -1

        # 处理后事
        self.Commit()
        self.CloseDatabase()

        # 内存数据库的话, 直接这样就行了
        if self.fname == ':memory:':
            return self.OpenDatabase(self.fname)

        # 存在关联文件的数据库, 优先使用删除文件再创建的形式, 如果失败, 
        # 重新打开并重建 schema
        try:
            os.remove(self.fname)
        except:
            PrintExcept("Failed to remove %s" % self.fname)
            # Reopen the database
            self.OpenDatabase(self.fname)
            # Drop the schema
            self.DropSchema()
            # Create the schema
            self.CreateSchema()
        else:
            # 正常情况下, 再打开这个文件作为数据库即可
            self.OpenDatabase(self.fname)

    def GetSchemaVersion(self):
        version = 0
        try:
            sql = "SELECT * FROM TAGS_VERSION;"
            for row in self.db.execute(sql):
                version = int(row[0])
                break
        except sqlite3.OperationalError:
            pass
        return version

    def Store(self, tagTree, dbFile = '', auto_commit = True, indicator = None):
        '''需要 tagTree

        tagTree 为标签的 path 树'''
        ret = False

        # 现阶段, 支持直接从标签文本保存
        if isinstance(tagTree, str):
            # 直接从字符串保存
            tags = tagTree

            if not dbFile and not self.fname:
                return False

            if not tags:
                return False

            self.OpenDatabase(dbFile) # 这里, 如果 dbFile 为空, 表示使用原来的
            try:
                updateList = [] # 不存在的直接插入, 存在的需要更新

                if auto_commit:
                    self.Begin()
                    #self.db.execute('begin;')

                tagList = tags.split('\n')
                tagListLen = len(tagList)
                for idx, line in enumerate(tagList):
                    # does not matter if we insert or update, 
                    # the cache must be cleared for any related tags

                    if indicator:
                        indicator(idx, tagListLen - 1)

                    tagEntry = TagEntry()
                    tagEntry.FromLine(line)

                    if not self.InsertTagEntry(tagEntry):
                        # 插入不成功?
                        # InsertTagEntry() 貌似是不会失败的?!
                        updateList.append(tagEntry)

                if auto_commit:
                    self.Commit()

                # Do we need to update?
                if updateList:
                    if auto_commit:
                        self.Begin()
                        #self.db.execute('begin;')

                    for i in updateList:
                        self.UpdateTagEntry(i)

                    if auto_commit:
                        self.Commit()
                ret = True
            except sqlite3.OperationalError:
                ret = False
                try:
                    if auto_commit:
                        self.db.rollback()
                except sqlite3.OperationalError:
                    pass
        else:
            pass
        return ret

    def StoreFromTagFile(self, tagFile, dbFile = '', auto_commit = True):
        '''从 tags 文件保存'''
        ret = False

        if not dbFile and not self.fname:
            return False

        if not tagFile:
            return False

        self.OpenDatabase(dbFile) # 这里, 如果 dbFile 为空, 表示使用原来的
        try:
            updateList = [] # 不存在的直接插入, 存在的需要更新

            if auto_commit:
                self.Begin()

            try:
                f = open(tagFile)
            except:
                return False

            for line in f:
                # does not matter if we insert or update, 
                # the cache must be cleared for any related tags
                if line.startswith('!'): # 跳过注释
                    continue

                tagEntry = TagEntry()
                tagEntry.FromLine(line)

                if not self.InsertTagEntry(tagEntry):
                    # 插入不成功?
                    # InsertTagEntry() 貌似是不会失败的?!
                    updateList.append(tagEntry)

                # enumerator 要双份，因为在两个作用域内有效
                # added on 2012-05-17
                #if tagEntry.GetParentType() == 'enum':
                #    tagEntryDup = tagEntry
                #    # 需要修改 scope 和 path
                #    scope = tagEntryDup.GetScope()
                #    # 扔掉 scope 最后的部分
                #    scope = '::'.join(scope.split('::')[:-1])
                #    if not scope:
                #        scope = '<global>'
                #    tagEntryDup.SetScope(scope)
                #    tagEntryDup.UpdatePath(scope)
                #    if not self.InsertTagEntry(tagEntryDup):
                #        updateList.append(tagEntryDup)

            if auto_commit:
                self.Commit()

            # Do we need to update?
            if updateList:
                if auto_commit:
                    self.Begin()

                for i in updateList:
                    self.UpdateTagEntry(i)

                if auto_commit:
                    self.Commit()

            f.close()
            ret = True
        except sqlite3.OperationalError:
            ret = False
            try:
                if auto_commit:
                    self.db.rollback()
            except sqlite3.OperationalError:
                pass

        return ret

    def SelectTagsByFile(self, file, dbFile = ''):
        '''取出属于 file 文件的全部标签'''
        # Incase empty dbFile is provided, use the current file name
        if not dbFile:
            dbFile = self.fname
        self.OpenDatabase(dbFile)

        sql = "select * from tags where file='" + file + "' "
        return self.DoFetchTags(sql)

    def DeleteByFileName(self, dbFile, fname, auto_commit = True):
        # [DEPRECATE]
        '''删除属于指定文件名 fname 的所有标签'''
        # make sure database is open
        try:
            self.OpenDatabase(dbFile)
            if auto_commit:
                self.Begin()
                #self.db.execute('begin;')

            self.db.execute("DELETE FROM TAGS WHERE file=?", (fname, ))

            if auto_commit:
                self.Commit()
        except:
            if auto_commit:
                self.db.rollback()

    def DeleteTagsByFiles(self, files, dbFile = '', auto_commit = True):
        '''删除属于指定文件名 fname 的所有标签'''
        ret = False
        # make sure database is open
        self.OpenDatabase(dbFile)
        try:
            if auto_commit:
                self.Begin()

            self.db.execute(
                "DELETE FROM tags WHERE file IN('%s')" % "', '".join(files))

            if auto_commit:
                self.Commit()
            ret = True
        except sqlite3.OperationalError:
            ret = False
            if auto_commit:
                self.db.rollback()
        return ret

    def UpdateTagsFileColumnByFile(self, newFile, oldFile, auto_commit = True):
        ret = False
        try:
            if auto_commit:
                self.Begin()
            self.db.execute("UPDATE TAGS set file=? WHERE file=?",
                            (newFile, oldFile))
            if auto_commit:
                self.Commit()
            ret = True
        except:
            ret = False
            if auto_commit:
                self.Rollback()
        return ret

    def Query(self, sql, dbFile = ''):
        '''Execute a query sql and return result set.

        这个函数特别之处在于自动 OpenDatabase()'''
        try:
            self.OpenDatabase(dbFile)
            return self.db.execute(sql)
        except:
            pass
        return [] # 具备迭代器的空对象

    def ExecuteUpdate(self, sql):
        try:
            self.db.execute(sql)
        except:
            pass

    def IsOpen(self):
        if self.db:
            return True
        else:
            return False

    def GetFiles(self, partialName = ''):
        files = []

        if not partialName:
            try:
                sql = "select * from files order by file"
                res = self.db.execute(sql)
                for row in res:
                    fe = FileEntry()
                    fe.SetId(row[0])
                    fe.SetFile(row[1])
                    fe.SetLastRetaggedTimestamp(row[2])
                    files.append(fe)
            except:
                pass
        else:
            try:
                matchPath = partialName and partialName.endswith(os.sep)
                tmpName = partialName.replace('_', '^_')
                sql = "select * from files where file like '%" + tmpName \
                        + "%' ESCAPE '^' "
                res = self.db.execute(sql)
                for row in res:
                    fe = FileEntry()
                    fe.SetId(row[0])
                    fe.SetFile(row[1])
                    fe.SetLastRetaggedTimestamp(row[2])

                    fname = fe.GetFile()
                    match = os.path.basename(fname)
                    if matchPath:
                        match = fname

                    # TODO: windows 下文件名全部保存为小写

                    if match.startswith(partialName):
                        files.append(fe)
            except:
                pass

        return files

    def GetFilesMap(self, matchFiles = []):
        '''返回文件到文件条目的字典, 方便比较'''
        filesMap = {}

        if not matchFiles:
            try:
                sql = "select * from files order by file"
                res = self.db.execute(sql)
                for row in res:
                    fe = FileEntry()
                    fe.SetId(row[0])
                    fe.SetFile(row[1])
                    fe.SetLastRetaggedTimestamp(row[2])
                    filesMap[fe.GetFile()] = fe
            except:
                pass
        else:
            try:
                sql = "select * from files where file in('%s')" \
                        % "','".join(matchFiles)
                res = self.db.execute(sql)
                for row in res:
                    fe = FileEntry()
                    fe.SetId(row[0])
                    fe.SetFile(row[1])
                    fe.SetLastRetaggedTimestamp(row[2])
                    filesMap[fe.GetFile()] = fe
            except:
                pass

        return filesMap

    def DeleteByFilePrefix(self, dbFile, filePrefix):
        try:
            self.OpenDatabase(dbFile)
            sql = "delete from tags where file like '" \
                    + filePrefix.replace('_', '^_') + "%' ESCAPE '^' "
            self.db.execute(sql)
        except:
            pass

    def DeleteFromFiles(self, files):
        if not files:
            return

        sql = "delete from FILES where file in ("
        for file in files:
            sql += "'" + file + "',"

        # remove last ','
        sql = sql[:-1] + ')'

        try:
            self.db.execute(sql)
        except:
            pass

    def DeleteFromFilesByPrefix(self, dbFile, filePrefix):
        try:
            self.OpenDatabase(dbFile)
            sql = "delete from FILES where file like '" \
                    + filePrefix.replace('_', '^_') + "%' ESCAPE '^' "
            self.db.execute(sql)
        except:
            pass

    def PPTokenFromSQlite3ResultSet(self, rs, token):
        pass

    def FromSQLite3ResultSet(self, row):
        '''从数据库的一行数据中提取标签对象
| id | name | file | fileid | line | kind | scope | parent_kind | access | inherits | signature | extra |
|----|------|------|--------|------|------|-------|-------------|--------|----------|-----------|-------|
|    |      |      |        |      |      |       |             |        |          |           |       |
'''
        entry = TagEntry()
        entry.id          = (row[0])
        entry.name        = (row[1])
        entry.file        = (row[2])
        entry.fileid      = int((row[3]))
        entry.line        = int((row[4]))

        entry.kind        = (row[5])
        entry.scope       = (row[6])
        entry.parent_kind = (row[7])
        entry.access      = (row[8])
        entry.inherits    = (row[9])
        entry.signature   = (row[10])
        entry.extra       = (row[11])

        return entry

    def _FetchTags(self, sql):
        pass

    def DoFetchTags(self, sql, kinds = []):
        '''从数据库中取出 tags'''
        tags = []

        if not kinds:
            if self.GetUseCache():
                # 尝试从缓存中获取
                tags = self.cache.Get(sql)
                if tags:
                    print '[CACHED ITEMS] %s\n' % sql
                    return

            try:
                exRs = self.Query(sql)

                # add results from external database to the workspace database
                for row in exRs:
                    tag = self.FromSQLite3ResultSet(row)
                    tags.append(tag)
            except:
                pass

            if self.GetUseCache():
                # 保存到缓存以供下次快速使用
                self.cache.Store(sql, tags)
        else:
            if self.GetUseCache():
                # 尝试从缓存中获取
                tags = self.cache.Get(sql, kinds)
                if tags:
                    print '[CACHED ITEMS] %s\n' % sql
                    return

            try:
                exRs = self.Query(sql)
                for row in exRs:
                    try:
                        kinds.index(row[7])
                    except ValueError:
                        continue
                    else:
                        tag = self.FromSQLite3ResultSet(row)
                        tags.append(tag)
            except:
                pass

            if self.GetUseCache():
                # 保存到缓存以供下次快速使用
                self.cache.Store(sql, tags, kinds)

        return tags

    def GetTagsByScopeAndName(self, scope, name, partialNameAllowed = False):
        if type(scope) == type(''):
            if not scope:
                return []

            tmpName = name.replace('_', '^_')

            sql = "select * from tags where "

            # did we get scope?
            if scope:
                sql += "scope='" + scope + "' and "

            # add the name condition
            if partialNameAllowed:
                sql += " name like '" + tmpName + "%' ESCAPE '^' "
            else:
                sql += " name ='" + name + "' "

            sql += " LIMIT " + str(self.GetSingleSearchLimit())

            # get the tags
            return self.DoFetchTags(sql)
        elif type(scope) == type([]):
            scopes = scope
            if not scopes:
                return []

            tmpName = name.replace('_', '^_')

            sql = "select * from tags where scope in("
            for i in scopes:
                sql += "'" + i + "',"
            sql = sql[:-1] + ") and "

            # add the name condition
            if partialNameAllowed:
                sql += " name like '" + tmpName + "%' ESCAPE '^' "
            else:
                sql += " name ='" + name + "' "

            # get the tags
            return self.DoFetchTags(sql)
        else:
            return []

    def GetOrderedTagsByScopesAndName(self, scopes, name, partialMatch = False):
        '''获取按名字升序排序后的 tags'''
        if not scopes:
            return []

        tmpName = name.replace('_', '^_')

        sql = "select * from tags where scope in("
        for i in scopes:
            sql += "'" + i + "',"
        sql = sql[:-1] + ") and "

        # add the name condition
        if partialMatch:
            sql += " name like '" + tmpName + "%' ESCAPE '^' "
        else:
            sql += " name ='" + name + "' "

        sql += 'order by name ASC'
        sql += ' LIMIT ' + str(self.GetSingleSearchLimit())

        # get the tags
        return self.DoFetchTags(sql)

    def GetTagsByScope(self, scope):
        sql = "select * from tags where scope='" + scope + "' limit " \
                + str(self.GetSingleSearchLimit())
        return self.DoFetchTags(sql)

    def GetTagsByKinds(self, kinds, orderingColumn, order):
        sql = "select * from tags where kind in ("
        for i in kinds:
            sql += "'" + i + "',"

        sql = sql[:-1] + ") "

        if orderingColumn:
            sql += "order by " + orderingColumn
            if order == ITagsStorage.OrderAsc:
                sql += " ASC"
            elif order == ITagsStorage.OrderDesc:
                sql += " DESC"
            else:
                pass

        return self.DoFetchTags(sql)

    def GetTagsByPath(self, path):
        if type(path) == type([]):
            sql = "select * from tags where path IN("
            for i in path:
                sql += "'" + i + "',"
            sql = sql[:-1] + ") "
            return self.DoFetchTags(sql)
        else:
            # FIXME: 为什么要 LIMIT 1 ？
            #sql = "select * from tags where path ='" + path + "' LIMIT 1"
            sql = "select * from tags where path ='%s'" % path
            # NOTE: 为什么？按照函数语义，不应该这么做，应该交给外层过滤
            #sql = "select * from tags where path ='%s' and kind != 'externvar' "\
                    #"LIMIT 1" % (path, )
            return self.DoFetchTags(sql)

    def GetTagsByPaths(self, paths):
        return self.GetTagsByPath(paths)

    def GetTagsByNameAndParent(self, name, parent):
        '''根据标签名称和其父亲获取标签'''
        sql = "select * from tags where name='" + name + "'"
        tags = self.DoFetchTags(sql)

        # 过滤掉不符合要求的标签
        return [i for i in tags if i.GetParent() == parent]

    def GetTagsByKindsAndPath(self, kinds, path):
        if not kinds:
            return []

        sql = "select * from tags where path='" + path + "'"
        return self.DoFetchTags(sql, kinds)

    def GetTagsByKindAndPath(self, kind, path):
        return self.GetTagsByKindsAndPath([kind], path)

    def GetTagsByFileAndLine(self, file, line):
        sql = "select * from tags where file='" + file \
                + "' and line=" + line + " "
        return self.DoFetchTags(sql)

    def GetTagsByScopeAndKind(self, scope, kind):
        return self.GetTagsByScopesAndKinds([scope], [kind])

    def GetTagsByScopeAndKinds(self, scope, kinds):
        if not kinds:
            return []

        sql = "select * from tags where scope='" + scope + "'"
        return self.DoFetchTags(sql, kinds)

    def GetTagsByKindsAndFile(self, kinds, fname, orderingColumn, order):
        if not kinds:
            return []

        sql = "select * from tags where file='" + fname + "' and kind in ("
        for i in kinds:
            sql += "'" + i + "',"
        sql = sql[:-1] + ")"

        if orderingColumn:
            sql += "order by " + orderingColumn
            if order == ITagsStorage.OrderAsc:
                sql += " ASC"
            elif order == ITagsStorage.OrderDesc:
                sql += " DESC"
            else:
                pass

        return self.DoFetchTags(sql)

    def DeleteFileEntry(self, fname):
        try:
            self.db.execute("DELETE FROM FILES WHERE file=?;", (fname, ))
            self.Commit()
        except sqlite3.OperationalError:
            return -1
        else:
            return 0

    def DeleteFileEntries(self, files):
        try:
            self.db.execute("DELETE FROM FILES WHERE file IN %s;" 
                            % MakeQMarkString(len(files)), tuple(files))
            self.Commit()
        except sqlite3.OperationalError:
            return -1
        else:
            return 0

    def InsertFileEntry(self, fname, tagtime, auto_commit = True):
        try:
            # 理论上, 不会插入失败
            self.db.execute("INSERT OR REPLACE INTO FILES VALUES(NULL, ?, ?);", 
                           (fname, tagtime))
            if auto_commit:
                self.Commit()
        except:
            return -1
        else:
            return 0

    def UpdateFileEntry(self, fname, tagtime, auto_commit = True):
        try:
            self.db.execute(
                "UPDATE OR REPLACE FILES SET tagtime=? WHERE file=?;", 
                (tagtime, fname))
            if auto_commit:
                self.Commit()
        except:
            return -1
        else:
            return 0

    def DeleteTagEntry(self, kind, signature, path):
        try:
            self.db.execute(
                "DELETE FROM TAGS WHERE Kind=? AND Signature=? AND Path=?", 
                (kind, signature, path))
            self.Commit()
        except:
            return False
        else:
            return True

    def InsertTagEntry(self, tag):
        if not tag.IsOk():
            return False

        if self.GetUseCache():
            self.ClearCache()

        try:
        #if 1:
            # INSERT OR REPLACE 貌似是不会失败的?!
            # 添加 parentType
            self.db.execute(
                "INSERT OR REPLACE INTO TAGS VALUES (NULL, "\
                "?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", 
                (tag.GetName(),
                 tag.GetFile(),
                 tag.GetLine(),
                 tag.GetText(),

                 tag.GetAccess(),
                 tag.GetInheritsAsString(),
                 tag.GetKind(),
                 tag.GetParent(),
                 tag.GetParentType(),
                 tag.GetPath(),
                 tag.GetReturn(),
                 tag.GetScope(),
                 tag.GetSignature(),
                 tag.GetTemplate(),
                 tag.GetTyperef()))
        except:
            return False
        else:
            return True

    def UpdateTagEntry(self, tag):
        if not tag.IsOk():
            return True

        if self.GetUseCache():
            self.ClearCache()

        try:
            # 添加 parentType
            self.db.execute(
                "UPDATE OR REPLACE TAGS SET "
                "name=?, file=?, line=?, text=?, "
                "access=?, inherits=?, kind=?, parent=?, parent_type=?"
                "path=?, return=?, scope=?, signature=?, template=?, typeref=?"
                "WHERE file=? AND kind=? AND path=? AND signature=?", 
                (tag.GetName(),
                 tag.GetFile(),
                 tag.GetLine(),
                 tag.GetText(),

                 tag.GetAccess(),
                 tag.GetInheritsAsString(),
                 tag.GetKind(),
                 tag.GetParent(),
                 tag.GetParentType(),
                 tag.GetPath(),
                 tag.GetReturn(),
                 tag.GetScope(),
                 tag.GetSignature(),
                 tag.GetTemplate(),
                 tag.GetTyperef(),

                 # where 这四个参数能唯一定位?
                 tag.GetFile(),
                 tag.GetKind(), 
                 tag.GetPath(), 
                 tag.GetSignature()))
            self.Commit()
        except:
            return False
        else:
            return True

    def IsTypeAndScopeContainer(self, typeName, scope):
        '''返回有三个元素的元组 (Ture/False, typeName, scope)
        True if type exist under a given scope.

        Incase it exist but under the <global> scope, 'scope' will be changed
        '''
        # Break the typename to 'name' and scope
        typeNameNoScope = typeName.rpartition(':')[2]
        scopeOne        = typeName.rpartition(':')[0]

        if scopeOne.endswith(':'):
            scopeOne = scopeOne[:-1]

        combinedScope = ''

        if scope != '<global>':
            combinedScope += scope

        if scopeOne:
            if combinedScope:
                combinedScope += '::'
            combinedScope += scopeOne

        sql = "select scope,kind from tags where name='" + typeNameNoScope + "'"

        foundGlobal = False

        try:
            for row in self.Query(sql):
                scopeFounded = row[0]
                kindFounded = row[1]
                containerKind = kindFounded == "struct" \
                        or kindFounded == "class"
                if scopeFounded == combinedScope and containerKind:
                    scope = combinedScope
                    typeName = typeNameNoScope
                    return True, typeName, scope
                elif scopeFounded == scopeOne and containerKind:
                    # this is equal to cases like this:
                    # class A {
                    #     typedef std::list<int> List;
                    #     List l;
                    # };
                    # the combinedScope will be: 'A::std'
                    # however, the actual scope is 'std'
                    scope = scopeOne
                    typeName = typeNameNoScope
                    return True, typeName, scope
                elif containerKind and scopeFounded == "<global>":
                    foundGlobal = True
        except:
            pass

        if foundGlobal:
            scope = "<global>"
            typeName = typeNameNoScope
            return True, typeName, scope

        return False, typeName, scope

    def IsTypeAndScopeExist(self, typeName, scope):
        bestScope = ''
        tmpScope = scope

        strippedName    = typeName.rpartition(':')[2]
        secondScope     = typeName.rpartition(':')[0]

        if secondScope.endswith(':'):
            secondScope = secondScope[:-1]

        if not strippedName:
            return False

        sql = "select scope,parent from tags where name='" + strippedName \
                + "' and kind in ('class', 'struct', 'typedef') LIMIT 50"
        foundOther = 0

        if secondScope:
            tmpScope += '::' + secondScope

        parent = tmpScope.rpartition(':')[2]

        try:
            for row in self.Query(sql):
                scopeFounded = row[0]
                parentFounded = row[1]

                if scopeFounded == tmpScope:
                    scope = scopeFounded
                    typeName = strippedName
                    return True
                elif parentFounded == parent:
                    bestScope = scopeFounded
                else:
                    foundOther += 1
        except:
            pass

        # if we reached here, it means we did not find any exact match
        if bestScope:
            scope = bestScope
            typeName = strippedName
            return True
        elif foundOther == 1:
            scope = scopeFounded
            typeName = strippedName
            return True

        return False

    def GetScopesFromFileAsc(self, fname, scopes):
        '''传入的 scopes 为列表'''
        sql = "select * from tags where file = '" + fname + "' " \
                + " and kind in('prototype', 'function', 'enum')" \
                + " order by scope ASC"

        # we take the first entry
        try:
            for row in self.Query(sql):
                scopes.append(row[0])
                break
        except:
            pass

    def GetTagsByFileScopeAndKinds(self, fname, scopeName, kinds):
        sql = "select * from tags where file = '" + fname + "' " \
                + " and scope='" + scopeName + "' "

        if kinds:
            sql += " and kind in("
            for i in kinds:
                sql += "'" + i + "',"
            sql = sql[:-1] + ")"

        return DoFetchTags(sql)

    def GetAllTagsNames(self):
        names = []
        try:
            sql = "SELECT distinct name FROM tags order by name ASC LIMIT " \
                    + str(self.GetMaxWorkspaceTagToColour())

            for row in self.Query(sql):
                # add unique strings only
                names.append(row[0])
        except:
            pass

        return names

    def GetTagsNames(self, kinds):
        if not kinds:
            return []

        names = []
        try:
            whereClause = " kind IN ("
            for kind in kinds:
                whereClause += "'" + kind + "',"

            whereClause = whereClause[:-1] + ") "

            sql = "SELECT distinct name FROM tags WHERE "
            sql += whereClause + " order by name ASC LIMIT " \
                    + str(self.GetMaxWorkspaceTagToColour())
            for row in self.Query(sql):
                names.append(row[0])
        except:
            pass

        return names

    def GetTagsByScopesAndKinds(self, scopes, kinds):
        if not kinds or not scopes:
            return []

        sql = "select * from tags where scope in ("
        for scope in scopes:
            sql += "'" + scope + "',"
        sql = sql[:-1] + ") "

        return self.DoFetchTags(sql, kinds)

    def GetGlobalFunctions(self):
        sql = "select * from tags where scope = '<global>' "\
                "AND kind IN ('function', 'prototype') LIMIT " \
                + str(self.GetSingleSearchLimit())
        return self.DoFetchTags(sql)

    def GetTagsByFiles(self, files):
        if not files:
            return []

        sql = "select * from tags where file in ("
        for file in files:
            sql += "'" + file + "',"
        sql = sql[:-1] + ")"
        return self.DoFetchTags(sql)

    def GetTagsByFilesAndScope(self, files, scope):
        if not files:
            return []

        sql = "select * from tags where file in ("
        for file in files:
            sql += "'" + file + "',"
        sql = sql[:-1] + ")"
        sql += " AND scope='" + scope + "'"
        return self.DoFetchTags(sql)

    def GetTagsByFilesKindsAndScope(self, files, kinds, scope):
        if not files:
            return []

        sql = "select * from tags where file in ("
        for file in files:
            sql += "'" + file + "',"
        sql = sql[:-1] + ")"

        sql += " AND scope='" + scope + "'"

        return self.DoFetchTags(sql, kinds)

    def GetTagsByFilesScopeTyperefAndKinds(self, files, kinds, scope, typeref):
        if not files:
            return []

        sql = "select * from tags where file in ("
        for file in files:
            sql += "'" + file + "',"
        sql = sql[:-1] + ")"

        sql += " AND scope='" + scope + "'"
        sql += " AND typeref='" + typeref + "'"

        return self.DoFetchTags(sql, kinds)

    def GetTagsByKindsLimit(self, kinds, orderingColumn, order, limit, partName):
        sql = "select * from tags where kind in ("
        for kind in kinds:
            sql += "'" + kind + "',"
        sql = sql[:-1] + ") "

        if orderingColumn:
            sql += "order by " + orderingColumn
            if order == ITagsStorage.OrderAsc:
                sql += " ASC"
            elif order == ITagsStorage.OrderDesc:
                sql += " DESC"
            else:
                pass

        if partName:
            tmpName = partName.replace('_', '^_')
            sql += " AND name like '%" + tmpName + "%' ESCAPE '^' "

        if limit > 0:
            sql += " LIMIT " + str(limit)

        return self.DoFetchTags(sql)

    def IsTypeAndScopeExistLimitOne(self, typeName, scope):
        path = ''

        # Build the path
        if scope and scope != "<global>":
            path += scope + "::"

        path += typeName
        sql += "select ID from tags where path='" + path \
                + "' and kind in ('class', 'struct', 'typedef') LIMIT 1"

        try:
            for row in self.Query(sql):
                return True
        except:
            pass

        return False

    def GetDereferenceOperator(self, scope):
        sql = "select * from tags where scope ='" + scope \
                + "' and name like 'operator%->%' LIMIT 1"
        return self.DoFetchTags(sql)

    def GetSubscriptOperator(self, scope):
        sql = "select * from tags where scope ='" + scope \
                + "' and name like 'operator%[%]%' LIMIT 1"
        return self.DoFetchTags(sql)

    def ClearCache(self):
        if self.cache:
            self.cache.Clear()


try:
    # 暂时用这种尝试方法
    import vim
    VIDEM_DIR = vim.eval('g:VidemDir')
except ImportError:
    print '%s: Can not get VidemDir, fallback to Linux case' % __file__
    # only for Linux
    VIDEM_DIR = os.path.expanduser('~/.videm')

if platform.system() == 'Windows':
    CTAGS = os.path.join(VIDEM_DIR, 'bin', 'vlctags2.exe')
else:
    CTAGS = os.path.join(VIDEM_DIR, 'bin', 'vlctags2')
CTAGS_OPTS = '--excmd=pattern --sort=no --fields=aKmSsnit '\
        '--c-kinds=+px --c++-kinds=+px'
CTAGS_OPTS_LIST = [
    '--excmd=pattern',
    '--sort=no',
    '--fields=aKmSsnit',
    '--c-kinds=+px',
    '--c++-kinds=+px',
]

# *DEPRECATE*
CPPTAGSDB = os.path.expanduser('~/bin/cpptagsdb')

# 强制视全部文件为 C++
CTAGS_OPTS += ' --language-force=c++'
CTAGS_OPTS_LIST += ['--language-force=c++']

def AppendCtagsOpt(opt):
    global CTAGS_OPTS, CTAGS_OPTS_LIST
    CTAGS_OPTS += ' ' + opt
    CTAGS_OPTS_LIST += [opt]

def IsCppSourceFile(fname):
    ext = os.path.splitext(fname)[1][1:]
    if ext in CPP_SOURCE_EXT:
        return True
    else:
        return False

def IsCppHeaderFile(fname):
    ext = os.path.splitext(fname)[1][1:]
    if ext in CPP_HEADER_EXT:
        return True
    else:
        return False

def ParseFiles(files, macrosFiles = []):
    '返回标签文本'
    if not files:
        return ''

    envDict = os.environ.copy()
    if macrosFiles: # 全局宏定义文件列表
        envDict['CTAGS_GLOBAL_MACROS_FILES'] = ','.join(macrosFiles)

    tags = ''
    if platform.system() == 'Windows':
        cmd = '"%s" %s -f - "%s"' % (CTAGS, CTAGS_OPTS, '" "'.join(files))
        p = subprocess.Popen(cmd, shell=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=envDict)
    else:
        #cmd = '"%s" %s -f - "%s"' % (CTAGS, CTAGS_OPTS, '" "'.join(files))
        cmd = [CTAGS] + CTAGS_OPTS_LIST + ['-f', '-'] + files
        # NOTE: 不用 shell，会快近两倍！
        p = subprocess.Popen(cmd, shell=False,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=envDict)

    # NOTE: 详见 python 手册关于 subprocess 的 warning
    out, err = p.communicate()
    tags = out

    if p.returncode != 0:
        print cmd
        print '%d: ctags occured some errors' % p.returncode
        print err

    return tags

def ParseFilesToTags(files, tagFile, macrosFiles = []):
    if platform.system() == 'Windows':
        # Windows 下的 cmd.exe 不支持过长的命令行
        batchCount = 10
    else:
        batchCount = 100
    totalCount = len(files)
    i = 0
    batchFiles = files[i : i + batchCount]
    firstEnter = True
    while batchFiles:
        if firstEnter:
            ret = _ParseFilesToTags(batchFiles, tagFile, macrosFiles,
                                    append = False)
            firstEnter = False
        else:
            ret = _ParseFilesToTags(batchFiles, tagFile, macrosFiles,
                                    append = True)
        if not ret:
            return ret
        i += batchCount
        batchFiles = files[i : i + batchCount]
    return True

def _ParseFilesToTags(files, tagFile, macrosFiles = [], append = False):
    '''append 为真时，添加新的tags到tagFile'''
    if not files or not tagFile:
        return False

    ret = True
    envDict = os.environ.copy()
    if macrosFiles:
        envDict['CTAGS_GLOBAL_MACROS_FILES'] = ','.join(macrosFiles)

    if platform.system() == 'Windows':
        if append:
            cmd = '"%s" -a %s -f "%s" "%s"' % (CTAGS, CTAGS_OPTS, tagFile,
                                               '" "'.join(files))
        else:
            cmd = '"%s" %s -f "%s" "%s"' % (CTAGS, CTAGS_OPTS, tagFile,
                                            '" "'.join(files))
        p = subprocess.Popen(cmd, shell=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=envDict)
    else:
        if append:
            cmd = [CTAGS, '-a'] + CTAGS_OPTS_LIST + ['-f', tagFile] + files
        else:
            cmd = [CTAGS] + CTAGS_OPTS_LIST + ['-f', tagFile] + files
        # NOTE: 不用 shell，会快近两倍！
        p = subprocess.Popen(cmd, shell=False,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=envDict)

    out, err = p.communicate()

    if p.returncode != 0:
        print cmd
        print '%d: ctags occured some errors' % p.returncode
        print err
        ret = False

    return ret

def CppTagsDbParseFilesAndStore(dbFile, files, macrosFiles = []):
    if not dbFile or not files:
        return False

    ret = True

    envDict = os.environ.copy()
    if macrosFiles:
        envDict['CTAGS_GLOBAL_MACROS_FILES'] = ','.join(macrosFiles)

    tags = ''
    if platform.system() == 'Windows':
        cmd = '"%s" %s -f - "%s" | "%s" -o "%s" -' \
                % (CTAGS, CTAGS_OPTS, '" "'.join(files),
                   CPPTAGSDB, dbFile)
    else:
        cmd = '"%s" %s -f - "%s" | "%s" -o "%s" -' \
                % (CTAGS, CTAGS_OPTS, '" "'.join(files),
                   CPPTAGSDB, dbFile)

    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, env=envDict)
    p.communicate()

    #print cmd

    if p.returncode != 0:
        print '%d: ctags and cpptagsdb occured some errors' % p.returncode
        ret = False

    return ret

def ParseFile(fname, macrosFiles = []):
    return ParseFiles([fname], macrosFiles)

def ParseFilesAndStore(storage, files, macrosFiles = [], filterNotNeed = True, 
                       indicator = None, useCppTagsDb = False,
                       onlyCpp = False):
    '''
    onlyCpp = False 表示不检查文件是否c++头文件或源文件'''
    # 确保打开了一个数据库
    if not storage.OpenDatabase():
        return

    if not files:
        return

    # NOTE: 全部转为绝对路径, 仅 parse C++ 头文件和源文件
    tmpFiles = []
    if onlyCpp:
        tmpFiles += [os.path.abspath(f) for f in files
                    if IsCppSourceFile(f) or IsCppHeaderFile(f)]
    else:
        tmpFiles += [os.path.abspath(f) for f in files]

    # 过滤不需要的. 通过比较时间戳
    if filterNotNeed:
        filesMap = storage.GetFilesMap(tmpFiles)
        mapLen = len(tmpFiles)
        idx = 0
        while idx < mapLen and filesMap:
            f = tmpFiles[idx]
            if filesMap.has_key(f):
                # 开始比较时间戳
                try:
                    mtime = int(os.path.getmtime(f))
                except OSError:
                    # 可能文件 f 不存在, 设置为 0, 即跳过
                    mtime = 0
                if filesMap[f].GetLastRetaggedTimestamp() >= mtime:
                    # 过滤掉
                    del tmpFiles[idx]
                    mapLen -= 1
                    continue
            idx += 1

    # 分批 parse
    totalCount = len(tmpFiles)
    batchCount = totalCount / 10
    if batchCount > 200: # 上限
        batchCount = 200
    if batchCount <= 0: # 下限
        batchCount = 1

    i = 0
    batchFiles = tmpFiles[i : i + batchCount]
    if indicator:
        indicator(0, 100)

    if useCppTagsDb:
        # 先删除全部需要更新的
        if not storage.DeleteTagsByFiles(batchFiles, auto_commit = True):
            print 'storage.DeleteTagsByFiles() failed'

    # 这个时间取尽量早的时间，理论上使用文件的修改时间戳比较好
    lastRetagTime = int(time.time())

    tagFileFd, tagFile = tempfile.mkstemp()
    while batchFiles:
        parseRet = True
        if useCppTagsDb:
            if not CppTagsDbParseFilesAndStore(
                storage.GetDatabaseFileName(), batchFiles, macrosFiles):
                print 'CppTagsDbParseFilesAndStore() failed'
        elif True:
            # 使用临时文件
            parseRet = ParseFilesToTags(batchFiles, tagFile, macrosFiles)
            if parseRet: # 只有解析成功才入库
                storage.Begin()
                if not storage.DeleteTagsByFiles(batchFiles,
                                                 auto_commit = False):
                    storage.Rollback()
                    storage.Begin()
                if not storage.StoreFromTagFile(tagFile, auto_commit = False):
                    storage.Rollback()
                    storage.Begin()
                timestamp = int(time.time())
                for f in batchFiles:
                    if os.path.isfile(f):
                        storage.InsertFileEntry(f, timestamp,
                                                auto_commit = False)
                storage.Commit()
        #else:
            #tags = ParseFiles(batchFiles, macrosFiles)
            #storage.Begin()
            #if not storage.DeleteTagsByFiles(batchFiles, auto_commit = False):
                #storage.Rollback()
                #storage.Begin()
            #if not storage.Store(tags, auto_commit = False, indicator = None):
                #storage.Rollback()
                #storage.Begin()
            #storage.Commit()

        if indicator:
            indicator(i, totalCount - 1)
        i += batchCount
        # 下一个 batchFiles
        batchFiles = tmpFiles[i : i + batchCount]

    os.close(tagFileFd)
    os.remove(tagFile)

    if indicator:
        indicator(100, 100)

    #for f in tmpFiles:
        #if os.path.isfile(f):
            #storage.InsertFileEntry(f, lastRetagTime)


def test():
    AppendCtagsOpt('-m')
    files = ['/usr/include/unistd.h', 'xstring.hpp']
    files = ['/usr/include/stdio.h', '/usr/include/stdlib.h']
    macrosFiles = ['global.h', 'global.hpp']
    storage = TagsStorageSQLite()

    #storage.OpenDatabase('test1.db')
    #storage.RecreateDatabase()
    #t1 = time.time()
    #ParseFilesAndStore(storage, files,
                       #filterNotNeed = False, useCppTagsDb = True)
    #t2 = time.time()
    #print "%f" % (t2 - t1)

    del files[:]
    with open('tags.files') as f:
        for line in f:
            files.append(line.strip())

    storage.OpenDatabase('test_vltags.db')
    storage.RecreateDatabase()
    t1 = time.time()
    ParseFilesAndStore(storage, files, macrosFiles,
                       filterNotNeed = False, useCppTagsDb = False)
    t2 = time.time()
    print "consume time: %f" % (t2 - t1)

    #print storage.GetFilesMap(['/usr/include/stdio.h',
                               #'/usr/include/unistd.h',
                               #'xstring.hpp'])
    #print storage.DeleteFileEntries(files)


if __name__ == '__main__':
    test()

