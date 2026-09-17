import os
import shutil

from debug_log import *
from configuration import *
from .extract_structure import extract_structure

# 支持的压缩包扩展名(按长度降序排列,确保 .tar.gz 优先于 .gz 匹配)
g_archive_extensions = ['.tar.gz', '.tar.bz2', '.tar.xz',
                        '.tgz', '.tbz2', '.txz', '.tar', '.zip', '.7z', '.rar']

# 支持的结构体类型
g_structure_types = ["struct", "union", "enum"]

# 允许处理的源文件扩展名
g_allowed_extensions = [".c", ".h"]

# 临时文件路径(从配置加载)
g_temp_file_path = CONFIG.get('temp_file_path')

class filePreprocessor:
        def __init__(self):
                self.temp_file_path = CONFIG.get('temp_file_path')

        # 处理文件内容:去除注释、压缩空格,返回预处理后的字符串
        # 此函数执行一次可能会被调用数百万次,拆分过多会形成额外开销,
        # 因此保持为单一函数实现。
        @staticmethod
        def _preprocess_content(f):
                processed = ""
                previous_was_space = False      # 标志最近的字符是不是空格
                in_block_comment = False        # 标志多行注释
                is_left_bracket = False
                after_pointer = False           # 标志刚输出指针符号*,需要吸收其后的空格
                bracket_depth = 0               # 数组维度[...]的嵌套深度,其内的*不做指针规整
                in_string_flag = None           # 内容是None, '"' 或 "'" 表示当前是否在字符串/字符常量中
                escape = False                  # 字符串中是否遇到转义字符
                in_preprocessor = False         # 标志是否在预处理命令中(跨行续接)

                # 以行为单位读取文件内容
                for line in f:
                        scan_flag = 0

                        # 检测预处理命令(行首为#)或预处理命令的续行,整行跳过
                        if not in_block_comment and in_string_flag is None:
                                if in_preprocessor:
                                        # 上一行以反斜杠结尾,当前行是续行
                                        stripped = line.rstrip()
                                        in_preprocessor = stripped.endswith('\\')
                                        continue
                                # 检查行首第一个非空白字符是否为#
                                temp_idx = 0
                                while temp_idx < len(line) and line[temp_idx] in ' \t':
                                        temp_idx += 1
                                if temp_idx < len(line) and line[temp_idx] == '#':
                                        # 回退预处理命令前的空格
                                        if processed and processed[-1] == ' ':
                                                processed = processed[:-1]
                                                previous_was_space = False
                                        stripped = line.rstrip()
                                        in_preprocessor = stripped.endswith('\\')
                                        continue

                        # 对每一行的字符进行分析
                        while scan_flag < len(line):
                                # 在字符串/字符常量内部,直接保留原始字符
                                if in_string_flag is not None:
                                        c = line[scan_flag]
                                        scan_flag += 1
                                        processed += c
                                        if escape:
                                                escape = False
                                        elif c == '\\':
                                                escape = True
                                        elif c == in_string_flag:
                                                in_string_flag = None
                                        continue

                                # 检查单行注释
                                if not in_block_comment and scan_flag + 1 < len(line) and line[scan_flag] == '/' and line[scan_flag + 1] == '/':
                                        # 回退注释前的空格
                                        if processed and processed[-1] == ' ':
                                                processed = processed[:-1]
                                                previous_was_space = False
                                        break  # 跳过这一行后面的内容

                                # 检查多行注释的开始
                                if not in_block_comment and scan_flag + 1 < len(line) and line[scan_flag] == '/' and line[scan_flag + 1] == '*':
                                        # 回退注释前的空格
                                        if processed and processed[-1] == ' ':
                                                processed = processed[:-1]
                                                previous_was_space = False
                                        in_block_comment = True
                                        scan_flag += 2
                                        continue

                                # 检查多行注释的结束
                                if in_block_comment and scan_flag + 1 < len(line) and line[scan_flag] == '*' and line[scan_flag + 1] == '/':
                                        in_block_comment = False
                                        scan_flag += 2
                                        continue

                                # 跳过多行注释中的内容
                                if in_block_comment:
                                        scan_flag += 1
                                        continue

                                # 跳过windows换行符
                                if line[scan_flag] == '\r' and scan_flag + 1 < len(line) and line[scan_flag + 1] == '\n':
                                        scan_flag += 2
                                        continue

                                # 跳过linux换行符
                                if line[scan_flag] == '\n':
                                        scan_flag += 1
                                        continue

                                # 跳过C语言换行符
                                if line[scan_flag] == '\\':
                                        scan_flag += 1
                                        continue

                                # 处理常规字符
                                c = line[scan_flag]
                                scan_flag += 1
                                # 检查是否进入字符串/字符常量
                                if c == '"' or c == "'":
                                        processed += c
                                        in_string_flag = c
                                        previous_was_space = False
                                        is_left_bracket = False
                                        after_pointer = False
                                        continue
                                if c == '{' or c == '(':
                                        processed += c
                                        is_left_bracket = True
                                        after_pointer = False
                                        continue

                                # 指针符号*:与类型之间保留一个空格,与变量名紧贴
                                # 数组维度[...]内的*是乘法或VLA占位符,按普通字符处理
                                if c == '*' and bracket_depth == 0:
                                        if processed and processed[-1] != ' ' and processed[-1] != '*' and not is_left_bracket:
                                                processed += ' '
                                        processed += '*'
                                        previous_was_space = False
                                        is_left_bracket = False
                                        after_pointer = True
                                        continue

                                # 制表符需要转换成空格
                                if c == '\t':
                                        c = ' '
                                if c == ' ':
                                        # 左括号或指针符号后的空格直接丢弃
                                        if is_left_bracket or after_pointer:
                                                continue
                                        # 出现过空格标记一次,避免添加多个空格
                                        elif not previous_was_space and processed:
                                                processed += c
                                                previous_was_space = True
                                                continue
                                else:
                                        # 记录数组维度括号深度,[...]内的*视为普通字符
                                        if c == '[':
                                                bracket_depth += 1
                                        elif c == ']' and bracket_depth > 0:
                                                bracket_depth -= 1
                                        # ')' 左侧的空格去掉
                                        if (c == ')' or c == '}') and processed and processed[-1] == ' ':
                                                processed = processed[:-1]
                                        processed += c
                                        previous_was_space = False
                                        is_left_bracket = False
                                        after_pointer = False
                                        continue

                return processed

        def preprocess(self, filename, root=None):
                try:
                        f = open(filename, 'r', encoding='utf-8', errors='replace')
                except IOError as e:
                        raise RuntimeError(_("无法打开输入文件:{}").format(filename)) from e

                processed = self._preprocess_content(f)
                f.close()

                if root is not None:
                        rel_path = os.path.relpath(os.path.abspath(filename), os.path.abspath(root))
                else:
                        rel_path = os.path.basename(filename)
                preprocessed_path = os.path.join(self.temp_file_path, rel_path) + '.tmp'

                # 按镜像结构逐级创建父目录后写入,同名文件因目录层级不同而不会互相覆盖
                os.makedirs(os.path.dirname(preprocessed_path), exist_ok=True)
                out_file = open(preprocessed_path, 'w', encoding='utf-8')
                out_file.write(processed)
                out_file.close()

                log_info(_("预处理完成,结果已保存到 {}").format(preprocessed_path))
                return True

class fileExtract:
        def __init__(self):
                self.preprocessor = filePreprocessor()

        # 提取单个文件中的结构体定义写数据库
        def _analysis_file(self, filepath, db_instance):
                # 删除临时目录前缀与 .tmp 后缀,还原为输入项目内的原始相对路径写入数据库
                path_without_tmp = filepath[:-4] if filepath.endswith('.tmp') else filepath
                source_file = os.path.relpath(path_without_tmp, g_temp_file_path)
                for structure_type in g_structure_types:
                        result = None
                        result = extract_structure(filepath, structure_type)
                        if not result:
                                log_warning(_("未找到 {} 定义").format(structure_type))
                        else:
                                for record in result:
                                        record['source_file'] = source_file
                                        db_instance.structures_insert(record)

        # 扫描指定文件路径,如果是目录,递归处理所有文件
        # scan_root 为预处理镜像的根目录,用于计算项目内相对路径
        def _scan_file_content(self, filepath, operation_type, db_instance=None, scan_root=None):
                if os.path.isdir(filepath):
                        for entry in os.listdir(filepath):
                                temp_filepath = os.path.join(filepath, entry)
                                self._scan_file_content(temp_filepath, operation_type, db_instance, scan_root)
                        return

                # 根据处理类型检查文件扩展名
                _, ext = os.path.splitext(filepath)
                if operation_type == "preprocess":
                        allowed_exts = g_allowed_extensions
                else:  # extract 模式处理预处理后的 .tmp 文件
                        allowed_exts = ['.tmp']
                if ext not in allowed_exts:
                        return

                if operation_type == "preprocess":
                        self.preprocessor.preprocess(filepath, scan_root)
                elif operation_type == "extract":
                        self._analysis_file(filepath, db_instance)
                return

        # 检查文件是否是支持的压缩包格式
        def _is_archive(self, filepath):
                lower_path = filepath.lower()
                for ext in g_archive_extensions:
                        if lower_path.endswith(ext):
                                return True
                return False

        # 去除压缩包文件名的扩展名,返回基础名称
        def _strip_archive_extension(self, filename):      
                lower_name = filename.lower()
                for ext in g_archive_extensions:
                        if lower_name.endswith(ext):
                                return filename[:-len(ext)]
                return filename

        # 使用 py7zr 解压 7z 压缩包(需安装: pip install py7zr)
        @staticmethod
        def _extract_7z(filepath, extract_dir):
                try:
                        import py7zr
                except ImportError:
                        raise RuntimeError(_("解压 .7z 需要 py7zr 库,请先安装: pip install py7zr"))
                with py7zr.SevenZipFile(filepath, mode='r') as archive:
                        archive.extractall(path=extract_dir)

        # 使用 rarfile 解压 rar 压缩包(需安装: pip install rarfile,并依赖系统 unrar)
        @staticmethod
        def _extract_rar(filepath, extract_dir):
                try:
                        import rarfile
                except ImportError:
                        raise RuntimeError(_("解压 .rar 需要 rarfile 库,请先安装: pip install rarfile"))
                with rarfile.RarFile(filepath) as archive:
                        archive.extractall(path=extract_dir)

        # 解压压缩包到指定目录,返回解压后的目录路径
        # 标准格式(zip/tar 系列)由 shutil 处理;7z、rar 等 Windows 常见格式依赖可选第三方库
        def _extract_archive(self, filepath, extract_dir):
                os.makedirs(extract_dir, exist_ok=True)
                lower_path = filepath.lower()

                if lower_path.endswith('.7z'):
                        self._extract_7z(filepath, extract_dir)
                elif lower_path.endswith('.rar'):
                        self._extract_rar(filepath, extract_dir)
                else:
                        shutil.unpack_archive(filepath, extract_dir)

                log_info(_("解压完成: {} -> {}").format(filepath, extract_dir))
                return extract_dir

        # 对输入路径进行类型判断,压缩包先解压,然后预处理并提取文件内容
        def extract_file(self, filepath, db_instance=None):
                if not os.path.exists(filepath):
                        raise FileNotFoundError(_("路径不存在: {}").format(filepath))

                # 类型判断: 压缩包 → 先解压
                is_archive = os.path.isfile(filepath) and self._is_archive(filepath)
                if is_archive:
                        archive_name = os.path.basename(filepath)
                        base_name = self._strip_archive_extension(archive_name)
                        extract_dir = os.path.join(g_temp_file_path, base_name)
                        log_info(_("检测到压缩包,正在解压: {}").format(filepath))
                        self._extract_archive(filepath, extract_dir)
                        filepath = extract_dir

                if is_archive:
                        scan_root = filepath
                else:
                        scan_root = os.path.dirname(os.path.abspath(filepath))
                self._scan_file_content(filepath, "preprocess", scan_root=scan_root)

                # 提取: 扫描 temp_file_path 下预处理后的 .tmp 文件
                self._scan_file_content(g_temp_file_path, "extract", db_instance)

_extract_file = fileExtract()

def preprocess_input_file(filepath, db_instance=None):
        return _extract_file.extract_file(filepath, db_instance)
