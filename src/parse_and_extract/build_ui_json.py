import os
import re
import sys
import json
import hashlib
from types import SimpleNamespace
from enum import IntEnum

from sqlite3_db import *
from debug_log import *

g_c_primitive_keywords = {
        'void', 'char', 'short', 'int', 'long',
        'float', 'double', 'signed', 'unsigned',
        '_Bool', 'bool'
}

g_c_qualifiers = {
        'const', 'static', 'volatile', 'register',
        'extern', 'auto', 'restrict', 'inline', '_Atomic'
}

g_c_composite_types = {
        'struct', 'union', 'enum'
}

class member_format(IntEnum):
        primitive = 1                   # 基本类型
        enum_list = 2                   # 枚举类型成员
        function_pointer = 3            # 函数指针
        composite_types = 4             # 复合类型
        nested_composite_types = 5      # 嵌套复合类型
        unknown = 6                     # 未知类型

class fillerRelations:
        def __init__(self,db):
                self.db = db

        # 将父结构 hash 与其成员引用的子结构 hash 按一对多关系写入 relations 表
        def fill_relations(self, parent_hash, child_hash_list):
                child_num = 0
                for child_hashes in child_hash_list:
                        for child_hash in child_hashes:
                                if child_hash is None:
                                        continue
                                self.db.relations_insert(parent_hash, child_hash)
                                child_num += 1
                # 写入关系后同步更新 structures 表中该父结构的 child_num 字段
                self.db.structures_update_child_num(parent_hash, child_num)
                log_debug(_("结构 {} 写入 {} 条子结构引用关系").format(parent_hash, child_num))

        # 遍历 structures 表,回填每个结构的parent_num字段,
        # 并将其父结构hash列表写入ui_json的table_head.parent_hash字段
        def fill_parent_hash(self):
                count = self.db.structures_get_count()
                log_info(_("开始回填 parent_num 与 parent_hash,共 {} 条记录").format(count))
                for i in range(1, count + 1):
                        record = self.db.structures_get_by_id(i)
                        if record is None:
                                log_warning(_("id={} 的记录不存在,跳过回填").format(i))
                                continue
                        parent_hashes = self.db.relations_get_parent_hashes(record['hash'])
                        self.db.structures_update_parent_num(record['hash'], len(parent_hashes))

                        # ui_json 为 JSON 字符串字段,解析后更新 table_head.parent_hash 再写回
                        if not record['ui_json']:
                                log_warning(_("id={} 的 ui_json 字段为空,跳过 parent_hash 回填").format(i))
                                continue
                        try:
                                ui_json = json.loads(record['ui_json'])
                        except json.JSONDecodeError as e:
                                log_error(_("id={} 的 ui_json 解析失败:{}").format(i, e))
                                continue
                        ui_json['table_head']['parent_hash'] = parent_hashes
                        ui_json_str = json.dumps(ui_json, ensure_ascii=False, separators=(',', ':'))
                        self.db.structures_update_ui_json(i, ui_json_str)

class structureParser:
        def __init__(self, db):
                self.db = db

        def _is_primitive_type(self, member):
                first_decl = member.split(',')[0]
                decl_words = []

                # 删除限定符
                for word in first_decl.split():
                        if word not in g_c_qualifiers:
                                decl_words.append(word)

                # 去掉末尾变量名
                if len(decl_words) > 1 :
                        type_info = decl_words[:-1]
                else:
                        type_info = decl_words

                # 空列表不算基础类型
                if not type_info:
                        return False, None

                # 只要有一个类型单词不是内建基础类型关键字,就不是基础类型
                for word in type_info:
                        if word not in g_c_primitive_keywords:
                                return False, None

                # 所有单词都是内建基础类型关键字
                return True, ' '.join(type_info)

        def _is_function_pointer(self, member):
                # 函数指针特征:"(*变量名)" 之后必须紧跟参数列表的 "("
                # 可命中 "(*fp)(int)"\"(*handlers[8])(void)"\"(**pfp)(void)" 等写法,
                # 并排除指向数组的指针 "(*name)[32]"\冗余括号指针 "(*p)" 等误判场景
                match = re.search(r'\(\s*\*[^)]*\)\s*\(', member)
                if match:
                        # data_type_first 取 "(*变量名)" 之前的字符串,即返回值类型(如 "int")
                        data_type_first = member[:match.start()].strip()
                        return True, data_type_first

                return False, None

        def _is_composite_types(self, member):
                type_words = []

                # 删除限定符
                for word in member.split():
                        if word not in g_c_qualifiers:
                                type_words.append(word)
                
                # 取首个类型关键字
                if type_words:
                        keyword = type_words[0]
                else:
                        keyword = ''
                        return False, None

                # 取 "关键字 + 其后第一个单词" 组成的字符串(如 "struct point")
                if keyword in g_c_composite_types:
                        return True, ' '.join(type_words[:2])

                return False, None
        
        def _is_nested_composite_types(self, member):
                brace_index = member.find('{')
                if brace_index != -1:
                        prefix_words = member[:brace_index].split()
                        if prefix_words:
                                keyword = prefix_words[0]
                        else:
                                keyword = ''
                                return False, None, None, None

                        # data_type_first 取 "关键字 + 其后第一个单词" 组成的字符串(如 "struct inner_pt")
                        data_type_first = ' '.join(prefix_words[:2])
                        if keyword in g_c_composite_types:
                                # "}" 之后为变量名部分,按 "," 切分为 data_name_list(如 "} p1, p2" -> ["p1", "p2"])
                                data_name_list = []
                                nested_content = None
                                end_brace_index = member.rfind('}')
                                if end_brace_index != -1:
                                        # nested_content 为最外层 "{}" 及其包裹的字符串(如 "{ int x; int y; }")
                                        nested_content = member[brace_index:end_brace_index + 1]
                                        for name in member[end_brace_index + 1:].split(','):
                                                name = name.strip()
                                                if name:
                                                        data_name_list.append(name)
                                return True, data_type_first, data_name_list, nested_content
                        elif keyword is not None:
                                return False, None, None, None

                # 不含 "{",不是内嵌复合类型
                return False, None, None, None

        # 函数指针专用切分:避免参数列表 "()" 内部的 "," 被误切(如 "void (*handler)(struct point *p, int count)")
        def _split_function_pointer_declarations(self, content, data_type_first):
                # 逐个跳过开头的限定符与 data_type_first 组成单词
                if data_type_first:
                        type_words = data_type_first.split()
                else:
                        type_words = []
                words = content.strip().split()
                index = 0
                for word in words:
                        if word in g_c_qualifiers:
                                index += 1
                        elif type_words and word == type_words[0]:
                                type_words.pop(0)
                                index += 1
                        else:
                                break

                prefix = ' '.join(words[:index])
                remainder = ' '.join(words[index:])

                member_content = []
                depth = 0
                start = 0
                for i, ch in enumerate(remainder):
                        if ch == '(':
                                depth += 1
                        elif ch == ')':
                                depth -= 1
                        elif ch == ',' and depth == 0:
                                field = remainder[start:i].strip()
                                if field:
                                        member_content.append(f"{prefix} {field}".strip())
                                start = i + 1

                field = remainder[start:].strip()
                if field:
                        member_content.append(f"{prefix} {field}".strip())
                return member_content

        # 将 content 剔除限定符与 data_type_first 前缀后按 "," 切分,每个字段前拼接含限定符的完整前缀
        # 返回拼接出的新字符串列表(如 "const int a, b" -> ["const int a", "const int b"])
        def _split_member_declarations(self, content, data_type_first):
                if data_type_first:
                        type_words = data_type_first.split()
                else:
                        type_words = []

                words = content.strip().split()
                index = 0
                for word in words:
                        if word in g_c_qualifiers:
                                index += 1
                        elif type_words and word == type_words[0]:
                                type_words.pop(0)
                                index += 1
                        else:
                                break

                # 前缀保留限定符与类型词的原始顺序(如 "const int")
                prefix = ' '.join(words[:index])
                remainder = ' '.join(words[index:])

                member_content = []
                for field in remainder.split(','):
                        member_content.append(f"{prefix} {field.strip()}".strip())
                return member_content

        def extrace_member_info(self, content, record):
                data_type_first = None
                member_info = SimpleNamespace(
                        data_type_first=None,
                        member_content=None,
                        nested_content=None
                )

                # member_format.enum_list
                if record['data_type_first'] == 'enum':
                        member_info.member_content = content
                        return member_format.enum_list, member_info

                # member_format.nested_composite_types
                ret, data_type_first, data_name_list, nested_content = \
                        self._is_nested_composite_types(content)
                if ret is True:
                        member_content = []
                        for data_name in data_name_list:
                                member_content.append(f"{data_type_first} {data_name}")

                        if not member_content:
                                member_content.append(data_type_first)
                        member_info.data_type_first = data_type_first
                        member_info.member_content = member_content
                        member_info.nested_content = nested_content
                        return member_format.nested_composite_types, member_info

                # member_format._is_function_pointer
                ret, data_type_first = self._is_function_pointer(content)
                if ret is True:
                        member_info.data_type_first = data_type_first
                        member_info.member_content = \
                                self._split_function_pointer_declarations(content, data_type_first)
                        return member_format.function_pointer, member_info

                # member_format.primitive
                ret, data_type_first = self._is_primitive_type(content)
                if ret is True:
                        member_info.data_type_first = data_type_first
                        member_info.member_content = \
                                self._split_member_declarations(content, data_type_first)
                        return member_format.primitive, member_info

                # member_format.composite_types
                ret, data_type_first = self._is_composite_types(content)
                if ret is True:
                        member_info.data_type_first = data_type_first
                        member_info.member_content = \
                                self._split_member_declarations(content, data_type_first)
                        return member_format.composite_types, member_info

                # member_format.unknown
                # 剔除限定符,取剩余单词中的第一个作为类型单词
                decl_words = []
                for word in content.split():
                        if word not in g_c_qualifiers:
                                decl_words.append(word)

                if decl_words:
                        data_type_first = decl_words[0]
                else:
                        data_type_first = None
                        log_warning(_("成员声明无法提取类型单词:{}").format(content))

                log_debug(_("成员归类为 unknown 类型:{}").format(content))
                member_info.data_type_first = data_type_first
                member_info.member_content = self._split_member_declarations(content, data_type_first)
                return member_format.unknown, member_info

        def extrace_member_hash(self, content, record):
                content_format, parsed_info = self.extrace_member_info(content, record)
                log_debug(_("成员解析结果:format={} data_type_first={} content={}").format(
                        content_format.name, parsed_info.data_type_first, content.strip()))
                member_info = SimpleNamespace(
                        child_hash=None,
                        member_content=parsed_info.member_content
                )

                if content_format == member_format.primitive \
                or content_format == member_format.enum_list \
                or content_format == member_format.function_pointer:
                        member_info.child_hash = None
                        return member_info

                if content_format == member_format.composite_types:
                        type_words = parsed_info.data_type_first.split()
                        # 首单词为 struct/enum/union 关键字,其余单词剔除前后 "*" 后拼接为结构名 data_type_latter
                        data_type_first = type_words[0]
                        data_name_words = []
                        for word in type_words[1:]:
                                word = word.strip('*')
                                if word:
                                        data_name_words.append(word)
                        # data_type_latter为剔除前后 "*" 的结构名
                        data_type_latter = ' '.join(data_name_words)

                        if data_type_latter:
                                member_info.child_hash = \
                                        self.db.structures_get_hashes_by_name(data_type_latter, data_type_first)
                                if not member_info.child_hash:
                                        log_warning(_("复合类型未在库中匹配到子结构:{} {}").format(data_type_first, data_type_latter))
                        else:
                                member_info.child_hash = None
                                log_warning(_("复合类型声明缺少结构名:{}").format(content))
                        return member_info

                if content_format == member_format.nested_composite_types:
                        # 取 data_type_first 首单词(struct/enum/union 关键字),配合 "{}" 主体内容精确反查内嵌结构 hash
                        if parsed_info.nested_content:
                                data_type_first = parsed_info.data_type_first.split()[0]
                                member_info.child_hash = \
                                        self.db.structures_get_hashes_by_type_and_content(data_type_first, parsed_info.nested_content)
                                if not member_info.child_hash:
                                        log_warning(_("内嵌复合类型未在库中匹配到子结构:{}").format(parsed_info.data_type_first))
                        else:
                                member_info.child_hash = None
                                log_warning(_("内嵌复合类型缺少 {{}} 主体内容:{}").format(content))
                        return member_info

                if content_format == member_format.unknown:
                        # 未知类型可能是 typedef 别名
                        if parsed_info.data_type_first:
                                member_info.child_hash = \
                                        self.db.structures_get_hashes_by_typedef_name(parsed_info.data_type_first)
                                if not member_info.child_hash:
                                        log_debug(_("未知类型按 typedef 名反查无匹配:{}").format(parsed_info.data_type_first))
                        else:
                                member_info.child_hash = None
                        return member_info

                return member_info

# 将数据库记录组装为 UI JSON
class uiJsonBuilder:
        def __init__(self, db):
                self.parser = structureParser(db)
                self.filler = fillerRelations(db)

        def _create_table_body(self, record):
                structue_content = record['content']
                if not structue_content or not structue_content.strip():
                        log_warning(_("结构 {} 的 content 字段为空,table_body 将为空").format(record['hash']))
                        structue_content = ''

                # 去除头尾的空白与最外层的 {}
                content = structue_content.strip()
                if content.startswith('{'):
                        content = content[1:]
                if content.endswith('}'):
                        content = content[:-1]

                table_body = {}
                member_index = 0
                parents_hash = record['hash']
                child_hash_list = []

                # enum 成员以 "," 作为间隔符,其余类型以 ";" 作为间隔符;嵌套的 {} 内部的间隔符不作为间隔符
                if record['data_type_first'] == 'enum':
                        separator = ','
                else:
                        separator = ';'

                segments = []
                depth = 0
                start = 0
                for i, ch in enumerate(content):
                        if ch == '{':
                                depth += 1
                        elif ch == '}':
                                depth -= 1
                        elif ch == separator and depth == 0:
                                segments.append(content[start:i])
                                start = i + 1

                # 收尾:最后一个间隔符之后的剩余部分
                segments.append(content[start:])

                for segment in segments:
                        # 空段(如多余分号)跳过
                        if not segment.strip():
                                continue

                        member_info = self.parser.extrace_member_hash(segment, record)
                        # child_hash 归一化为列表
                        if member_info.child_hash:
                                child_hash = member_info.child_hash
                        else:
                                child_hash = [None]

                        # member_content 归一化为列表
                        if isinstance(member_info.member_content, list):
                                member_content_list = member_info.member_content
                        else:
                                member_content_list = [member_info.member_content]

                        # 同一段拆出的多个成员共用同一个child_hash,逐个占用独立的 member_index
                        for member_content in member_content_list:
                                member_index += 1
                                table_body[f'members_{member_index}'] = {
                                        'member_context': member_content,
                                        'child_hash': child_hash,
                                }

                        child_hash_list.append(child_hash)
                self.filler.fill_relations(parents_hash, child_hash_list)

                return table_body

        def _create_table_head(self, record):
                typedef_name = record['typedef_names'].split(',') if record['typedef_names'] else []
                # parent_hash:暂保持为空列表
                parent_hash = []
                table_head = {
                        'type': record['data_type_first'],
                        'name': record['data_type_latter'],
                        'typedef_name': typedef_name,
                        'hash': record['hash'],
                        'parent_hash': parent_hash,
                }
                return table_head

        def _create_ui_json(self, record):
                table_head = self._create_table_head(record)
                table_body = self._create_table_body(record)

                ui_json = {
                        'table_head': table_head,
                        'table_body': table_body,
                }

                ui_json_str = json.dumps(ui_json, ensure_ascii=False, separators=(',', ':'))

                return ui_json_str

# 将数据库记录转换为 UI JSON并更新 ui_json 字段
def fill_ui_json_and_relations_table(db_instance):
        count = db_instance.structures_get_count()
        log_info(_("开始生成 UI JSON 并填充关系表,数据库元素个数:{}").format(count))

        builder = uiJsonBuilder(db_instance)
        for i in range(1, count + 1):
                record = db_instance.structures_get_by_id(i)
                if record is not None:
                        ui_json_str = builder._create_ui_json(record)
                        db_instance.structures_update_ui_json(i, ui_json_str)
                else:
                        log_warning(_("id={} 的记录不存在,跳过 UI JSON 生成").format(i))
                        continue

        # 所有关系写入完毕后,回填每个结构的 parent_num 与 ui_json 中的 parent_hash
        builder.filler.fill_parent_hash()
        log_info(_("UI JSON 生成与关系表填充完成"))
        return
