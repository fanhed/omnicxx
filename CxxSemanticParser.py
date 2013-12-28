#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os
import os.path
import json
import re

# 这个正则表达式经常要用
CXX_MEMBER_OP_RE = re.compile('^(\.|->|::)$')

########## 硬编码设置, 用于快速测试 ##########
path = [os.path.expanduser('~/.videm/core'),
        os.path.expanduser('~/.vim/bundle/videm/autoload/omnicpp'),
        os.path.expanduser('~/.vim/autoload/omnicpp')]
sys.path.extend(path)

# NOTE 不在 vim 环境下运行, 默认使用 "~/libCxxParser.so"
import CppParser

##########

# CPP_OP 作为 CPP_OPERATORPUNCTUATOR 的缩写
from CppTokenizer import CPP_EOF, CPP_KEYOWORD, CPP_WORD, C_COMMENT,        \
        C_UNFIN_COMMENT, CPP_COMMENT, CPP_STRING, CPP_CHAR, CPP_DIGIT,      \
        CPP_OPERATORPUNCTUATOR as CPP_OP
from CppTokenizer import CxxTokenize

from ListReader import ListReader
from CxxTypeParser import TokensReader
from CxxTypeParser import CxxType
from CxxTypeParser import CxxUnitType
from CxxTypeParser import CxxParseType
from CxxTypeParser import CxxParseTemplateList

class ComplScope(object):
    '''
    代码补全时每个scope的信息, 只有三种:
    * ->    成员变量, (成员)函数的返回值, 需要解析出具体的类型
    * .     成员变量, (成员)函数的返回值, 需要解析出具体的类型
    * ::    一个固定的类型, 无需解析类型, 可以称之为容器

    {
        'kind': <'container'|'variable'|'function'|'unknown'>
        'text': <name>    <- 必然是单元类型 eg. A<a,b,c>
        'tmpl': <template initialization list>
        'tag' : {}        <- 在解析的时候添加
        'type': {}        <- 在解析的时候添加
        'cast': <强制类型转换>
    }
    '''
    KIND_CONTAINER  = 0
    KIND_VARIABLE   = 1
    KIND_FUNCTION   = 2
    KIND_UNKNOWN    = 3

    kind_mapping = {
        KIND_CONTAINER  : 'KIND_CONTAINER',
        KIND_VARIABLE   : 'KIND_VARIABLE',
        KIND_FUNCTION   : 'KIND_FUNCTION',
        KIND_UNKNOWN    : 'KIND_UNKNOWN',
    }

    def __init__(self, name = '', kind = KIND_UNKNOWN):
        self.text = ''
        self.kind = type(self).KIND_UNKNOWN
        # 每个item是文本
        self.tmpl = []
        # CxxType
        self.type = None
        # CxxType
        self.cast = None

    def __repr__(self):
        return '{"text": "%s", "kind": "%s", "tmpl": %s, "type": %s, "cast": %s}' % (
            self.text, type(self).kind_mapping.get(self.kind),
            self.tmpl, self.type, self.cast)

class ComplInfo(object):
    def __init__(self):
        # ComplScope
        self.scopes = []
        # 如果最前面的有效 token 为 '::', 那么这个成员为 True
        # 最前面的 '::' 不算进 scopes 里面的类型里面, 
        # 单独算进这个变量处理起来更简单
        self._global = False
        # "new A::B", 用于标识, 有时候需要根据这个来获取calltips
        self.new_stmt = False

    def Invalidate(self):
        del self.scopes[:]

    def __repr__(self):
        return '{"global": %s, "new_stmt": %s, "scopes": %s}' % (
            self._global, self.new_stmt, self.scopes)

# 跳至指定的匹配，tokrdr 当前的 token 为 left 的下一个
# 返回结束时的嵌套层数, 调用着可检查此返回值来确定是否下一步动作
def SkipToMatch(tokrdr, left, right, collector = None):
    nestlv = 1
    while tokrdr.curr.IsValid():
        tok = tokrdr.Get()

        if isinstance(collector, list):
            collector.append(tok)

        if tok.text == left:
            nestlv += 1
        elif tok.text == right:
            nestlv -= 1

        if nestlv == 0:
            break

    return nestlv

class TypeInfo(object):
    '''代表一个C++类型，保存足够的信息, vim omnicpp 兼容形式'''
    def __init__(self):
        self.text = ''
        self.tmpl = []
        self.typelist = []

def ParseTypeInfo(tokrdr):
    '''
    从一条语句中获取变量信息, 无法判断是否非法声明
    尽量使传进来的参数是单个语句而不是多个语句
    Return: 若解释失败，返回无有效内容的 TypeInfo
    eg1. const MyClass&
    eg2. const map < int, int >&
    eg3. MyNs::MyClass
    eg4. ::MyClass**
    eg5. MyClass a, *b = NULL, c[1] = {};
    eg6. A<B>::C::D<E<Z>, F>::G g;
    eg7. hello(MyClass1 a, MyClass2* b
    eg8. Label: A a;
    TODO: eg9. A (*a)[10];
    '''
    pass

def GetComplInfo(tokens):
    # 需要语法解析, 实在是太麻烦了
    '''
    获取全能补全请求前的语句的 ComplInfo
        case01. A::B C::D::|
        case02. A::B()->C().|
        case03. A::B().C->|
        case04. A->B().|
        case05. A->B.|
        case06. Z Y = ((A*)B)->C.|
        case07. (A*)B()->C.|
        case08. static_cast<A*>(B)->C.|
        case09. A(B.C()->|)

        case10. ::A->|
        case11. A(::B.|)
        case12. (A**)::B.|
    '''
    rdr = TokensReader(tokens[::-1])
    #while rdr.curr.IsValid():
        #print rdr.Pop()

    # 初始状态, 可能不用
    STATE_INIT = 0
    # 期待操作符号 '->', '.', '::'
    STATE_EXPECT_OP = 1
    # 期待单词
    STATE_EXPECT_WORD = 2

    state = STATE_INIT

    global CXX_MEMBER_OP_RE

    result = ComplInfo()

    # 用于模拟 C 语言的 for(; x; y) 语句
    __first_enter = True
    while rdr.curr.IsValid():
        if not __first_enter:
            # 消耗一个token
            rdr.Pop()
        __first_enter = False

        if rdr.curr.kind == CPP_OP and CXX_MEMBER_OP_RE.match(rdr.curr.text):
        # 这是个成员操作符 '->', '.', '::'
            if state == STATE_INIT:
                # 初始状态遇到操作符, 补全开始, 光标前没有输入单词
                state = STATE_EXPECT_WORD
            elif state == STATE_EXPECT_OP:
                state = STATE_EXPECT_WORD
            elif state == STATE_EXPECT_WORD:
                # 语法错误
                result.Invalidate()
                break
            else:
                pass
            # endif

        elif rdr.curr.kind == CPP_WORD:
            if state == STATE_INIT:
                # 这是base, 这里不考虑base的问题, 继续
                pass
            elif state == STATE_EXPECT_OP:
                # 期望操作符, 遇到单词
                # 结束.
                # eg A::B C::|
                #       ^
                break
            elif state == STATE_EXPECT_WORD:
                # 成功获取一个单词
                compl_scope = ComplScope()
                compl_scope.text = rdr.curr.text
                # 无法根据上一个字符来判断类型, 因为 ::A 的 A 可能是变量
                # 根据下一个字符来判断: prev -> right
                if rdr.prev.text == '::':
                    compl_scope.kind = ComplScope.KIND_CONTAINER
                elif rdr.prev.text == '->' or rdr.prev.text == '.' \
                        or rdr.prev.text == '[':
                    compl_scope.kind = ComplScope.KIND_VARIABLE
                else:
                    # unknown
                    pass

                result.scopes.insert(0, compl_scope)
                state = STATE_EXPECT_OP
            else:
                # 忽略
                pass

        elif rdr.curr.kind == CPP_KEYOWORD and rdr.curr.text == 'this':
            if state == STATE_INIT:
                # 这是base, 忽略
                state = STATE_EXPECT_OP
            elif state == STATE_EXPECT_OP:
                # 期待操作符, 即上一个是单词, 显然语法错误
                result.Invalidate()
                break
            elif state == STATE_EXPECT_WORD:
                # 期待单词遇到 this, 肯定是 this-> 之类的
                right = rdr.prev
                #if right.text != '->': # 现在是不做指针或者结构的区别的
                    #result.Invalidate()
                    #break
                # 特殊变量 'this'
                compl_scope = ComplScope()
                compl_scope.text = 'this'
                compl_scope.kind = type(compl_scope).KIND_VARIABLE
                result.scopes.insert(0, compl_scope)
                break
            else:
                pass
            # endif

            #if state == STATE_INIT:
            #    pass
            #elif state == STATE_EXPECT_OP:
            #    pass
            #elif state == STATE_EXPECT_WORD:
            #    pass
            #else:
            #    pass
            ## endif

        elif rdr.curr.kind == CPP_OP and rdr.curr.text == ')':
            if state == STATE_INIT:
                # 括号后是无法补全的
                result.Invalidate()
                break
            elif state == STATE_EXPECT_OP:
                # 期待操作符, 遇到右括号
                # 必定是一个 postcast, 结束
                # 无须处理, 直接完成
                # eg. (A*)B->|
                #        ^
                break
            elif state == STATE_EXPECT_WORD:
                # 期待单词
                # 遇到右括号
                # 可能是 precast 或者 postcast 或者是一个函数
                # precast:
                #   ((A*)B.b)->C.|
                #           ^|
                #   ((A*)B.b())->C.|
                #            ^|
                #   static_cast<A *>(B.b())->C.|
                #                        ^|
                #   
                # postcast:
                #   (A)::B.|
                #     ^|
                #
                # function:
                #   func<T>(0).|
                #            ^|
                # 
                save_prev = rdr.prev
                rdr.Pop()
                colltoks = []
                SkipToMatch(rdr, ')', '(', colltoks)
                # tmprdr 是正常顺序, 最后的 '(' 字符不要
                if colltoks:
                    colltoks.pop(-1)
                colltoks.reverse()
                tmprdr = TokensReader(colltoks)

                '''
                C++形式的cast:
                    dynamic_cast < type-id > ( expression )
                    static_cast < type-id > ( expression )
                    reinterpret_cast < type-id > ( expression )
                    const_cast < type-id > ( expression )
                '''

                # 处理模板
                #   Func<T>(0)
                #         ^
                tmpltoks = []
                if rdr.curr.text == '>':
                    tmpltoks.append(rdr.Pop())
                    if SkipToMatch(rdr, '>', '<', tmpltoks) != 0:
                        result.Invalidate()
                        break
                    # 需要反转
                    tmpltoks.reverse()

                if rdr.curr.kind == CPP_WORD:
                    # 确定是函数
                    compl_scope = ComplScope()
                    compl_scope.kind = ComplScope.KIND_FUNCTION
                    compl_scope.text = rdr.curr.text
                    if tmpltoks:
                        compl_scope.tmpl = CxxParseTemplateList(TokensReader(tmpltoks))
                    result.scopes.insert(0, compl_scope)
                    state = STATE_EXPECT_OP
                elif rdr.curr.kind == CPP_KEYOWORD and \
                        rdr.curr.text == 'dynamic_cast' or \
                        rdr.curr.text == 'static_cast' or \
                        rdr.curr.text == 'reinterpret_cast' or \
                        rdr.curr.text == 'const_cast':
                    # C++ 形式的 precast
                    if not tmpltoks:
                        # 语法错误
                        result.Invalidate()
                        break
                    compl_scope = ComplScope()
                    compl_scope.kind = ComplScope.KIND_VARIABLE
                    compl_scope.text = '<CODE>'
                    # 解析的时候不要前后的尖括号
                    tmpltoks = tmpltoks[1:-1]
                    tmpltoks_reader = TokensReader(tmpltoks)
                    cxx_type = CxxParseType(tmpltoks_reader)
                    compl_scope.cast = cxx_type
                    result.scopes.insert(0, compl_scope)
                    break
                elif tmprdr.curr.text == '(':
                    # C 形式的 precast
                    #   ((A*)B.b)->C.|
                    #           ^|
                    compl_scope = ComplScope()
                    compl_scope.kind = ComplScope.KIND_VARIABLE
                    compl_scope.text = '<CODE>' # 无需名字

                    # 既然是 precast 那么这里可以直接获取结果并结束
                    tmprdr.Pop()
                    colltoks = []
                    SkipToMatch(tmprdr, '(', ')', colltoks)
                    # 不要最后的 ')'
                    if colltoks:
                        colltoks.pop(-1)
                    # 这里就可以解析类型了
                    cxx_type = CxxParseType(TokensReader(colltoks))
                    # cxx_type 可能是无效的, 由外部检查
                    compl_scope.cast = cxx_type
                    result.scopes.insert(0, compl_scope)
                    break
                elif rdr.prev.kind == CPP_OP and rdr.prev.text == '::':
                    # postcast
                    # eg. (A**)::B.|
                    #         |^^
                    if result.scopes:
                        compl_scope = result.scopes[0]
                    else:
                        compl_scope = ComplScope()
                    if not compl_scope.type:
                        # 这种情况下, compl_scope 肯定可以分析处理type的, 
                        # 如果没有那肯定是语法错误
                        result.Invalidate()
                        break
                    compl_scope.type._global = True
                    break
                else:
                    #  (A**)::B.
                    # ^
                    if save_prev.text == '::':
                        result._global = True
                    else:
                        result.Invalidate()

                    break
            else:
                pass

        elif rdr.curr.kind == CPP_OP and rdr.curr.text == ']':
            # 处理数组下标
            # eg. A[B][C[D]].|
            # 暂不支持数组下标补全, 现在全忽略掉 
            if state == STATE_INIT:
                result.Invalidate()
                break
            elif state == STATE_EXPECT_OP:
                result.Invalidate()
                break
            elif state == STATE_EXPECT_WORD:
                # 跳过所有连续的 [][][]
                brk = False
                while rdr.curr.kind == CPP_OP and rdr.curr.text == ']':
                    rdr.Pop()
                    if SkipToMatch(rdr, ']', '[') != 0:
                        # 中括号不匹配, 肯定语法错误吧
                        result.Invalidate()
                        brk = True
                        break
                if brk:
                    break
                # NOTE: 当前指向下一个token, 如果这时候continue的话, 这个token
                #       将错误检查, 因为每次循环后固定获取下一个token, 所以要
                #       put一个token
                rdr.Put(rdr.prev)
            else:
                result.Invalidate()
                break
            # endif

        elif rdr.curr.kind == CPP_OP and rdr.curr.text == '>':
            # 处理模板实例化
            # eg. A<B, C>::|
            if state == STATE_INIT:
                result.Invalidate()
                break
            elif state == STATE_EXPECT_OP:
                # eg. if (1 > A.|)
                break
            elif state == STATE_EXPECT_WORD:
                # eg. A<B, C>::
                #           ^
                # eg. if (a > ::A.
                #           ^
                # 跳到匹配的 '<'
                right = rdr.prev
                tmpltoks = [rdr.Pop()]
                if SkipToMatch(rdr, '>', '<', tmpltoks) != 0:
                    # eg. if (a > ::A.
                    if right.text == '::':
                        result._global = True
                    break
                tmpltoks.reverse()
                # 分析模板
                tmpl = CxxParseTemplateList(TokensReader(tmpltoks))
                # 继续往前分析, 因为现在的状况基本是已确定的(?)
                # 前面必须是一个函数或者容器
                if not rdr.curr.kind == CPP_WORD and \
                   not rdr.curr.kind == CPP_KEYOWORD and \
                   right.text != '::': # 无此语法: A<B>.
                    # 貌似必然是语法错误, 因为没见过这种语法: {op}<>
                    result.Invalidate()
                    break
                compl_scope = ComplScope()
                compl_scope.text = rdr.curr.text
                compl_scope.tmpl = tmpl
                # 上面已经检查过了, 这里可用于调试
                if right.text == '::':
                    compl_scope.kind = ComplScope.KIND_CONTAINER
                result.scopes.insert(0, compl_scope)
                state = STATE_EXPECT_OP
            else:
                result.Invalidate()
                break
            # endif

        else:
        # 遇到了其他字符, 结束. 前面判断的结果多数情况下是有用
            right = rdr.prev
            if right.kind == CPP_OP and right.text == '::':
                # 期待单词时遇到其他字符, 并且之前的是 '::', 那么这是 <global>
                if state == STATE_EXPECT_WORD:
                    result._global = True

            if rdr.curr.kind == CPP_KEYOWORD and rdr.curr.text == 'new':
                result.new_stmt = True

            break

        # endif

    # endwhile

    # eg. ::A->|
    if state == STATE_EXPECT_WORD and rdr.prev.text == '::':
        result._global = True

    return result

class ScopeInfo(object):
    '''
    NOTE: 理论上可能会有嵌套名空间的情况, 但是为了简化, 不允许使用嵌套名空间
        eg.
            using namespace A;
            using namespace B;
            A::B::C <-> C
    '''
    def __init__(self):
        # 函数作用域, 一般只用名空间信息
        self.function = []
        # 容器的作用域列表, 包括名空间信息
        self.container = []
        # 全局(文件)的作用域列表, 包括名空间信息
        # 因为 global 是 python 的关键词, 所以用这个错别字
        self._global = []

    def Print(self):
        print 'function: %s' % self.function
        print 'container: %s' % self.container
        print 'global: %s' % self._global

def Error(msg):
    print msg

def unit_test_GetComplInfo():
    cases = [
        # test
        #"dynamic_cast<A<Z, Y, X> *>(B.b())->C.",

        # general
        "A::B C::D::",
        "A::B()->C().",
        "A::B().C->",
        "A->B().",
        "A->B.",
        "Z Y = ((A*)B)->C.",
        "(A*)B()->C.",
        "static_cast<A*>(B)->C.",
        "A(B.C()->",
        "(A**)::B.",
        "B<X,Y>(Z)->",
        "A<B>::C<D, E>::F.g.",
        "A(B.C()->",
        "A(::B.C()->",

        # global
        "::A->",
        "A(::B.",

        # precast
        "((A*)B.b)->C.",
        "((A*)B.b())->C.",
        "dynamic_cast<A<Z, Y, X> *>(B.b())->C.",

        # 数组
        "A[B][C[D]].",

        # 模板实例化
        "A<B, C>::",

        # 终结
        "if (a > ::A.",

        # this
        "if ( this->a.",

        # new
        "A::B *pa = new A::",
    ]
    
    for origin in cases:
        tokens = CxxTokenize(origin)
        #print tokens
        print '=' * 40
        print origin
        #print tokens
        compl_info = GetComplInfo(tokens)
        print compl_info
        #print json.dumps(eval(repr(compl_info)), sort_keys=True, indent=4)

def main(argv):
    unit_test_GetComplInfo()

if __name__ == '__main__':
    import sys
    ret = main(sys.argv)
    if ret is None:
        ret = 0
    sys.exit(ret)
