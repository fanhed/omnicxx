#!/usr/bin/env python
# -*- encoding:utf-8 -*-

import re
#from Misc import Obj2Dict, Dict2Obj

CKinds = {
    'c': "class",     
    'd': "macro",     
    'e': "enumerator",
    'f': "function",  
    'g': "enum",      
    'l': "local",     
    'm': "member",    
    'n': "namespace", 
    'p': "prototype", 
    's': "struct",    
    't': "typedef",   
    'u': "union",     
    'v': "variable",  
    'x': "externvar", 
}

RevCKinds = {
    "class"     : 'c',
    "macro"     : 'd',
    "enumerator": 'e',
    "function"  : 'f',
    "enum"      : 'g',
    "local"     : 'l',
    "member"    : 'm',
    "namespace" : 'n',
    "prototype" : 'p',
    "struct"    : 's',
    "typedef"   : 't',
    "union"     : 'u',
    "variable"  : 'v',
    "externvar" : 'x',
}

def ToFullKind(kind):
    if len(kind) > 1:
        return kind
    return CKinds.get(kind, 'unknown')
def ToFullKinds(kinds):
    return [ToFullKind(kind) for kind in kinds]

def ToAbbrKind(kind):
    if len(kind) == 1:
        return kind
    return RevCKinds.get(kind, ' ')
def ToAbbrKinds(kinds):
    return [ToAbbrKind(kind) for kind in kinds]

ACCESS_MAPPING = {
    'public'    : '+',
    'protected' : '#',
    'private'   : '-',
}

ACCESS_RMAPPING = {
    '+': 'public',
    '#': 'protected',
    '-': 'private',
}

def ToAbbrAccess(access):
    if len(access) == 1:
        return access
    ACCESS_MAPPING.get(access, ' ')
def ToFullAccess(access):
    if len(access) > 1:
        return access
    ACCESS_RMAPPING.get(access, 'unknown')
    

reMacroSig = r'^\s*#\s*define\s*[a-zA-Z_]\w*(\(.*?\))' # 包括括号
patMacroSig = re.compile(reMacroSig)
def GetMacroSignature(srcLine):
    global patMacroSig
    m = patMacroSig.match(srcLine)
    if m:
        return m.group(1)
    else:
        return ''

def GenPath(scope, name):
    if scope == '<global>':
        return name
    return '%s::%s' % (scope, name)

class TagEntry():
    def __init__(self):
        '''
        与数据库保持一致

        file    未定

        text    不再需要
        pattern 不再需要
        parent  不再需要保存, 直接从 scope 提取
        path    不再需要保存, 直接根据 scope 和 name 合并生成即可

        kind    保存缩写
        '''
        self.id = -1                # unused

        self.name = ''              # Tag name (short name, excluding any scope 
                                    # names)
        self.file = ''              # File this tag is found
        self.fileid = 0             # 对应 FILES 表的 id
        self.line = -1              # Line number
        #self.text = ''              # code text

        #self.pattern = ''           # A pattern that can be used to locate the 
                                    # tag in the file

        self.kind = 'unknown'     # Member, function, class, typedef etc.
        #self.parent = ''            # Direct parent
        #self.path = ''              # Tag full path
        self.scope = ''             # Scope

        # 对于 typedef, 存储原型
        # 对于 struct 和 class, 存储模板和特化信息
        # 对于 function, 存储声明和模板
        # 对于 variable, 存储声明
        # 上述存储模板信息的时候, 如果存在模板特化, 则要把文本存储到符号">"为止
        self.extra = ''

        self.exts = {}              # Additional extension fields

    def ToDict(self):
        return Obj2Dict(self, set(['differOnByLineNumber']))

    def FromDict(self, d):
        Dict2Obj(self, d)

    def Create(self, name, fname, line, text, kind, exts, pattern = ''):
        '''
        @kind:  全称
        '''
        self.SetId(-1)
        self.SetName(name)
        self.SetFile(fname)
        self.SetLine(line)
        if kind:
            self.SetKind(kind)
        self.exts = exts

        extra = ''

        if kind == 'typedef':
            extra = re.sub(r'typedef\s+|\s+[a-zA-Z_]\w*\s*;\s*$', '', text)
        elif kind == 'struct' or kind == 'class':
            m = re.search(r'\btemplate\s*<.*>', text)
            if m:
                extra = m.group()
        elif kind == 'function':
            '''
            A<B>::C & func(void) {}
            template<> A<B>::C *** func (void) {}
            template<class T> A<B>::C *** func <X, Y> (void) {}
            '''
            m = re.search(r'([^(]+)\(', text)
            if m:
                extra = re.sub(r'\s*[a-zA-Z_]\w*$', '', m.group(1).strip())
        elif kind == 'variable' or kind == 'externvar':
            # TODO: 数组形式未能解决, 很复杂, 暂时无法完善处理, 全部存起来
            if exts.has_key('typeref'):
                # 从这个域解析
                # typeref:struct:ss    } ***p, *x;
                extra = exts['typeref'].partition(':')[2]
                extra += re.sub(r'^\s*}\s*', '', text)
            else:
                extra = text
        else:
            pass

        self.extra = extra

        # Check if we can get full name (including path)
        # 添加 parent_kind 属性, 以保证不丢失信息
        scope = ''
        if self.GetExtField('class'):
            self.SetParentKind('class')
            scope = self.GetExtField('class')
        elif self.GetExtField('struct'):
            self.SetParentKind('struct')
            scope = self.GetExtField('struct')
        elif self.GetExtField('namespace'):
            self.SetParentKind('namespace')
            scope = self.GetExtField('namespace')
        elif self.GetExtField('union'):
            self.SetParentKind('union')
            scope = self.GetExtField('union')
        elif self.GetExtField('enum'):
            self.SetParentKind('enum')
            # enumerator 的 scope 和 path 要退一级
            scope = '::'.join(self.GetExtField('enum').split('::')[:-1])
        else:
            pass

        if not scope:
            scope = '<global>'
        self.SetScope(scope)

        if kind == 'macro':
            sig = GetMacroSignature(pattern[2:-2])
            if sig:
                self.SetSignature(sig)

    def FromLine(self, strLine):
        strLine = strLine
        line = -1
        text = ''
        exts = {}

        # get the token name
        partStrList = strLine.partition('\t')
        name = partStrList[0]
        strLine = partStrList[2]

        # get the file name
        partStrList = strLine.partition('\t')
        fileName = partStrList[0]
        strLine = partStrList[2]

        # here we can get two options:
        # pattern followed by ;"
        # or
        # line number followed by ;"
        partStrList = strLine.partition(';"\t')
        if not partStrList[1]:
            # invalid pattern found
            return

        if strLine.startswith('/^'):
            # regular expression pattern found
            pattern = partStrList[0]
            strLine = '\t' + partStrList[2]
        else:
            # line number pattern found, this is usually the case when
            # dealing with macros in C++
            pattern = partStrList[0].strip()
            strLine = '\t' + partStrList[2]
            line = int(pattern)

        # next is the kind of the token
        if strLine.startswith('\t'):
            strLine = strLine.lstrip('\t')

        partStrList = strLine.partition('\t')
        kind = partStrList[0]
        strLine = partStrList[2]

        if strLine:
            for i in strLine.split('\t'):
                key = i.partition(':')[0].strip()
                val = i.partition(':')[2].strip()

                if key == 'line' and val:
                    line = int(val)
                elif key == 'text': # 不把 text 放到扩展域里面
                    text = val
                else:
                    exts[key] = val

        # 真的需要?
        #kind = kind.strip()
        #name = name.strip()
        #fileName = fileName.strip()
        #pattern = pattern.strip()

        if kind == 'enumerator':
            # enums are specials, they are not really a scope so they should 
            # appear when I type: enumName::
            # they should be member of their parent 
            # (which can be <global>, or class)
            # but we want to know the "enum" type they belong to, 
            # so save that in typeref,
            # then patch the enum field to lift the enumerator into the 
            # enclosing scope.
            # watch out for anonymous enums -- leave their typeref field blank.
            if exts.has_key('enum'):
                typeref = exts['enum']
                # comment on 2012-05-17
                #exts['enum'] = \
                        #exts['enum'].rpartition(':')[0].rpartition(':')[0]
                if not typeref.rpartition(':')[2].startswith('__anon'):
                    # watch out for anonymous enums
                    # just leave their typeref field blank.
                    exts['typeref'] = 'enum:%s' % typeref

        self.Create(name, fileName, line, text, kind, exts, pattern)

    def IsValid(self):
        return self.kind != 'unknown'

    def IsContainer(self):
        kind = self.GetKind()
        return kind == 'class' \
                or kind == 'struct' \
                or kind == 'union' \
                or kind == 'namespace'

    def IsConstructor(self):
        if self.GetKind() != 'function' and self.GetKind() != 'prototype':
            return False
        else:
            return self.GetName() == self.GetScope()

    def IsDestructor(self):
        if self.GetKind() != 'function' and self.GetKind() != 'prototype':
            return False
        else:
            return self.GetName().startswith('~')

    def IsMethod(self):
        '''Return true of the this tag is a function or prototype'''
        return self.IsPrototype() or self.IsFunction()

    def IsFunction(self):
        return self.GetKind() == 'function'

    def IsPrototype(self):
        return self.GetKind() == 'prototype'

    def IsMacro(self):
        return self.GetKind() == 'macro'

    def IsClass(self):
        return self.GetKind() == 'class'

    def IsStruct(self):
        return self.GetKind() == 'struct'

    def IsScopeGlobal(self):
        return not self.GetScope() or self.GetScope() == '<global>'

    def IsTypedef(self):
        return self.GetKind() == 'typedef'


    #------------------------------------------
    # Operations
    #------------------------------------------
    def GetId(self):
        return self.id
    def SetId(self, id):
        self.id = id

    def GetName(self):
        return self.name
    def SetName(self, name):
        self.name = name

    def GetPath(self):
        return GenPath(self.scope, self.name)

    def GetFile(self):
        return self.file
    def SetFile(self, file):
        self.file = file

    def GetLine(self):
        return self.line
    def SetLine(self, line):
        self.line = line

    def SetKind(self, kind):
        self.kind = ToAbbrKind(kind)
    def GetKind(self):
        return self.GetFullKind()
    def GetAbbrKind(self):
        return self.kind
    def GetFullKind(self):
        return ToFullKind(self.kind)

    def GetParentKind(self):
        return ToFullKind(self.GetExtField('parent_kind'))
    def SetParentKind(self, parent_kind):
        self.exts['parent_kind'] = ToAbbrKind(parent_kind)

    def GetAccess(self):
        return self.GetExtField("access")
    def SetAccess(self, access):
        self.exts["access"] = access

    def GetSignature(self):
        return self.GetExtField("signature")
    def SetSignature(self, sig):
        self.exts["signature"] = sig

    def SetInherits(self, inherits):
        self.exts["inherits"] = inherits
    def GetInherits(self):
        return self.GetInheritsAsString()

    def GetTyperef(self):
        return self.GetExtField("typeref")
    def SetTyperef(self, typeref):
        self.exts["typeref"] = typeref

    def GetInheritsAsString(self):
        return self.GetExtField('inherits')

    def GetInheritsAsArrayNoTemplates(self):
        '''返回清除了模版信息的继承字段的列表'''
        inherits = self.GetInheritsAsString()
        parent = ''
        parentsArr = []

        # 清楚所有尖括号内的字符串
        depth = 0
        for ch in inherits:
            if ch == '<':
                if depth == 0 and parent:
                    parentsArr.append(parent.strip())
                    parent = ''
                depth += 1
            elif ch == '>':
                depth -= 1
            elif ch == ',':
                if depth == 0 and parent:
                    parentsArr.append(parent.strip())
                    parent = ''
            else:
                if depth == 0:
                    parent += ch

        if parent:
            parentsArr.append(parent.strip())

        return parentsArr

    def GetInheritsAsArrayWithTemplates(self):
        inherits = self.GetInheritsAsString()
        parent = ''
        parentsArr = []

        depth = 0
        for ch in inherits:
            if ch == '<':
                depth += 1
                parent += ch
            elif ch == '>':
                depth -= 1
                parent += ch
            elif ch == ',':
                if depth == 0 and parent:
                    parentsArr.append(parent.strip())
                    parent = ''
                elif depth != 0:
                    parent += ch
            else:
                parent += ch

        if parent:
            parentsArr.append(parent.strip())

        return parentsArr

    def GetReturn(self):
        return self.GetExtField('return')
    def SetReturn(self, retVal):
        self.exts["return"] = retVal

    def GetScope(self):
        return self.scope
    def SetScope(self, scope):
        self.scope = scope

    def Key(self):
        '''Generate a Key for this tag based on its attributes

        Return tag key'''
        # 键值为 [原型/宏:]path:signature
        key = ''
        if self.GetKind() == 'prototype' or self.GetKind() == 'macro':
            key += self.GetKind() + ': '

        key += self.GetPath() + self.GetSignature()
        return key

    def TypeFromTyperef(self):
        '''Return the actual type as described in the 'typeref' field

        return real name or wxEmptyString'''
        typeref = self.GetTyperef()
        if typeref:
            name = typeref.partition(':')[0]
            return name
        else:
            return ''

    # ------------------------------------------
    #  Extenstion fields
    # ------------------------------------------
    def GetExtField(self, extField):
        return self.exts.get(extField, '')

    # ------------------------------------------
    #  Misc
    # ------------------------------------------
    def Print(self):
        '''顺序基本与数据库的一致'''
        print '======================================'
        print 'Name:\t\t' + self.GetName()
        print 'File:\t\t' + self.GetFile()
        print 'Line:\t\t' + str(self.GetLine())
        print 'Kind:\t\t' + self.GetKind()
        print 'Path:\t\t' + self.GetPath()
        print 'Scope:\t\t' + self.GetScope()
        print '---- Ext Fields ----'
        for k, v in self.exts.iteritems():
            if k == 'parent_kind':
                v = ToFullKind(v)
            print k + ':\t\t' + v
        print '--------------------------------------'

    def UpdatePath(self, scope):
        '''Update the path with full path (e.g. namespace::class)'''
        pass

if __name__ == '__main__':
    import sys
    import os.path
    for fname in sys.argv[1:]:
        if not os.path.exists(fname):
            print '%s not found' % fname
            continue
        with open(fname) as f:
            for line in f:
                if line.startswith('!'):
                    continue
                print line,
                entry = TagEntry()
                entry.FromLine(line)
                entry.Print()

    assert GetMacroSignature('#define MIN(x, y) x < y ? x : y') == '(x, y)'
    entry = TagEntry()
    lines = [
        'ab\tmain.c\t/^  ab,$/;"\tenumerator\tline:12\tenum:abc\ttext:ab',
        'xy\tmain.c\t/^  int xy;$/;"\tmember\tline:16\tstruct:xyz\taccess:public\ttext:int xy;'
    ]
    for line in lines:
        entry.FromLine(line)
        entry.Print()

