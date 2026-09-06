import os
import sys
import shutil
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from i18n import *
from build_svg import *
from debug_log import *
from sqlite3_db import *
from configuration import *
from parse_and_extract import *

# 定位配置文件路径
def resolve_config_file(cli_config):
        config_file = None

        cwd_config = os.path.join(os.getcwd(), 'config.json')
        if os.path.isfile(cwd_config):
                config_file = cwd_config

        subdir_config = os.path.join(os.getcwd(), 'config', 'config.json')
        if os.path.isfile(subdir_config):
                config_file = subdir_config

        if cli_config:
                config_file = cli_config

        return config_file

# 预解析 --config/--lang 并提前初始化界面语言
def preload_i18n():
        preparser = argparse.ArgumentParser(prog=g_prog, add_help=False)
        preparser.add_argument('--config', default=None)
        preparser.add_argument('--lang', choices=list(SUPPORTED_LANGUAGES), default=None)
        pre_args, _unknown = preparser.parse_known_args()

        config_file = resolve_config_file(pre_args.config)
        if config_file:
                set_config_file(config_file)

        lang = pre_args.lang
        if lang is None:
                # 仅读取语言字段,完整配置由 load_config 统一加载
                lang = CONFIG._load_raw().get('language')
        setup_i18n(lang)

# 根据命令行参数更新全局配置,先加载指定的配置文件(若有),再用命令行参数覆盖对应字段。
def load_config(args):
        config_file = resolve_config_file(args.config)

        if config_file:
                set_config_file(config_file)
        
        # 加载配置文件中的配置项,如果不存在配置文件则使用默认配置
        for key, value in CONFIG._load_raw().items():
                set_assignment_config(key, value)

        if args.lang is not None:
                set_assignment_config('language', args.lang)

        if args.mode is not None:
                set_assignment_config('run_mode', args.mode)
        run_mode = CONFIG.get('run_mode')

        if run_mode == 'init' and args.input_file is not None:
                set_assignment_config('init_mode', {'input_file': args.input_file})

        if run_mode == 'svg' and args.extract_name is not None:
                set_assignment_config('svg_mode', {'extract_name': args.extract_name})

        if args.db_path is not None or args.db_name is not None:
                database = {}
                if args.db_path is not None:
                        database['db_path'] = args.db_path
                if args.db_name is not None:
                        database['db_name'] = args.db_name
                set_assignment_config('database', database)

        debug_log = {}
        if args.log_enabled is not None:
                debug_log['enabled'] = args.log_enabled

        if args.log_level is not None:
                debug_log['level'] = args.log_level

        if args.log_console is not None:
                debug_log['enable_console'] = args.log_console

        if (args.log_level is not None or args.log_console is not None) and 'enabled' not in debug_log:
                debug_log['enabled'] = True

        if debug_log:
                set_assignment_config('debug_log', debug_log)


"""解析命令行参数

--lang: 可选,界面语言 zh_CN/en,覆盖配置项 language
--mode: 可选,取值 init/svg,未传入时使用配置默认值 run_mode
--config: 可选,指定配置文件路径,未提供时使用默认配置
--input-file: 可选,指定待分析的输入文件/目录 (input_file),仅在 init 时生效
--db-path: 可选,指定数据库输出路径 (database.db_path)
--log-enabled/--no-log-enabled: 可选,是否启用调试日志 (debug_log.enabled)
--log-level: 可选,调试日志级别 (debug_log.level)
--log-console/--no-log-console: 可选,日志是否输出到控制台 (debug_log.enable_console)
-v/--version: 打印程序版本号并退出
--help: 打印帮助信息并退出

所有参数均以 -- 为前缀,并需以 --参数名=值 的形式传入。
命令行参数的优先级高于配置文件,未指定的参数不会覆盖已有配置。
"""
def parse_args():
        parser = argparse.ArgumentParser(
                prog=g_prog,
                description=_('C结构体拓扑分析工具'),
                formatter_class=lambda prog: argparse.RawDescriptionHelpFormatter(prog, max_help_position=100),)

        # lang 为可选参数,覆盖配置项 language
        parser.add_argument('--lang', choices=list(SUPPORTED_LANGUAGES), default=None,
                                help=_('界面语言:zh_CN 或 en(可选,覆盖配置项 language)'))
        # mode 为可选参数,取值 init/svg,未传入时使用配置默认值 run_mode
        parser.add_argument('--mode', choices=['init', 'svg'], default=None,
                                help=_('程序流程:init 解析输入文件并构建数据库;svg 基于已有数据生成 SVG(可选,未指定时使用配置默认值 run_mode)'))
        parser.add_argument('--config',
                                help=_('配置文件路径(可选,未指定时使用默认配置)'))
        parser.add_argument('--input-file',
                                help=_('待分析的输入文件/目录路径 (init_mode.input_file),仅在 init 时生效'))
        parser.add_argument('--extract-name',
                                help=_('待提取的根结构体名称 (svg_mode.extract_name),仅在 svg 时生效'))
        parser.add_argument('--db-path',
                                help=_('数据库输出路径 (database.db_path)'))
        parser.add_argument('--db-name',
                                help=_('数据库文件名 (database.db_name)'))
        parser.add_argument('--log-enabled', action=argparse.BooleanOptionalAction, default=None,
                                help=_('是否启用调试日志 (debug_log.enabled)'))
        parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                                help=_('调试日志级别 (debug_log.level)'))
        parser.add_argument('--log-console', action=argparse.BooleanOptionalAction, default=None,
                                help=_('日志是否输出到控制台 (debug_log.enable_console)'))
        # version 动作与 -h 同为早退参数,打印版本后直接退出
        parser.add_argument('-v', '--version', action='version', version=f'%(prog)s {g_version}',
                                help=_('显示程序版本号并退出'))
        return parser.parse_args()

# 初始化数据库、日志等运行时资源
def init_resources():
        temp_file_path = CONFIG.get('temp_file_path')
        db = create_memory_db()

        init_log()
        db.init_db()

        return temp_file_path, db

def init():
        # 校验待分析的输入文件配置:缺失时打印 error 日志并抛出异常,
        # 且必须在 init_resources 之前检查,避免 finally 落盘产生空数据库
        input_path = CONFIG.get('init_mode').get('input_file')
        if input_path is None or not str(input_path).strip():
                msg = _("未配置待分析的输入文件 (init_mode.input_file)")
                log_error(msg)
                raise ValueError(msg)

        temp_file_path, db = init_resources()

        try:
                # 确保预处理目录存在(temp_file_path 为目录占位符,不可作为文件打开)
                os.makedirs(temp_file_path, exist_ok=True)
                preprocess_input_file(input_path, db)
                fill_ui_json_and_relations_table(db)
        finally:
                db.flush_to_disk()
                db.close()
                close_log()
                shutil.rmtree(temp_file_path, ignore_errors=True)

def create_svg():
        # 读取待提取的结构体名称:svg_mode.extract_name
        extract_name = CONFIG.get('svg_mode').get('extract_name')
        if not extract_name or not extract_name.strip():
                raise ValueError(_("未配置待提取的结构体名称 (svg_mode.extract_name)"))

        init_log()
        db = create_disk_db()
        try:
                # 按空格拆分:两个单词时前者为 data_type_first、后者为 data_type_latter,
                # 通过 structures_get_hashes_by_name 精确匹配
                parts = extract_name.split()
                if len(parts) >= 2:
                        data_type_first, data_type_latter = parts[0], parts[1]
                        hashes = db.structures_get_hashes_by_name(data_type_latter, data_type_first)
                else:
                        # 单个单词视为 data_type_latter,先在 data_type_latter 列大小写敏感精确匹配
                        data_type_latter = parts[0]
                        hashes = db.structures_get_hashes_by_data_type_latter(data_type_latter)
                        # 未命中则退回按 typedef_name 匹配
                        if not hashes:
                                hashes = db.structures_get_hashes_by_typedef_name(data_type_latter)

                if not hashes:
                        raise ValueError(_("未在数据库中找到匹配的结构体: {}").format(extract_name))

                # build_svg 一次仅接收一个 hash,解析到多个 hash 时逐个调用；
                # 数据库连接复用本函数打开的 db,全程只打开一次
                for h in hashes:
                        build_svg(h, db)

        finally:
                db.close()
                close_log()

def main():
        # 预解析并初始化语言,使 parse_args 阶段的 -h 帮助文本遵循配置文件语言
        preload_i18n()
        args = parse_args()
        load_config(args)
        # 配置/命令行(--lang)确定最终语言后,重新初始化 i18n 使其生效
        setup_i18n()

        # 运行模式:命令行优先,未指定时使用配置模块默认值
        model = CONFIG.get('run_mode')

        if model == 'init':
                log_critical(_("运行模式: init"))
                init()
                log_critical(_("运行完成"))
        elif model == 'svg':
                log_critical(_("运行模式: svg"))
                create_svg()
                log_critical(_("运行完成"))
        else:
                # 配置文件中的 run_mode 不受 argparse choices 保护,须显式校验
                msg = _("无效的运行模式 (run_mode): {},仅支持 init/svg").format(model)
                log_error(msg)
                raise ValueError(msg)

if __name__ == '__main__':
        try:
                main()
        except Exception as e:
                # 捕获内部抛出的异常,打印友好错误信息后以非0退出码结束
                log_critical(_("错误: {}").format(e))
                sys.exit(1)