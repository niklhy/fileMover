#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 番号文件名和文件夹名匹配类

"""番号匹配模块 - 提取和匹配文件/文件夹名称中的编号"""

import re
from typing import Optional, Dict

class PatternMatcher:
    """番号提取与匹配器"""

    def __init__(self):
        # 预编译正则提高性能
        # 注意：这些模式用于match()，要求从开头匹配
        self.patterns = [
            (re.compile(r'^([fF][cC]2?[_-]?[pP][pP][vV][_-]?(\d{6,}))'), 'fc2_ppv'),
            (re.compile(r'^([fF][cC]2?[_-]?(\d{6,}))'), 'fc2'),
            # 新增：匹配以数字+字母开头的番号（如 300MIUM-1185, 259LUXU-1416）
            (re.compile(r'^(\d+[a-zA-Z]+[_-]\d+)'), 'digit_prefix'),
            (re.compile(r'^([a-zA-Z]+\d*[_-]\d+)'), 'standard'),
            (re.compile(r'^(\d{3,}[_-]\d{3,})'), 'numeric'),
        ]

        # 新增：用于search()的模式，可在字符串任意位置查找番号
        # 这些模式更严格，确保找到的是真正的番号而非随机字符组合
        # 按优先级排序：越具体的模式越靠前
        self.search_patterns = [
            # 1. 数字前缀+字母+连字符+数字（如 300MIUM-1185）- 最高优先级，避免被 standard_search 截断
            (re.compile(r'(\d+[a-zA-Z]{2,}-\d+)'), 'digit_prefix_search'),
            # 2. 标准连字符格式
            (re.compile(r'([a-zA-Z]{2,}\d*-\d{3,})'), 'standard_search'),      # 如 MXGS-1397, SSIS-001, IPX-123, S1-12345
            # 3. 纯数字-纯数字的番号格式（如 Caribbeancom 的 120225-001）
            (re.compile(r'(\d{5,}-\d{2,})'), 'numeric_search'),                 # 如 120225-001, 100618-001
            # 4. FC2 系列
            (re.compile(r'(fc2[_-]?\d{6,})', re.I), 'fc2_search'),              # 如 fc2-123456, FC2_123456
            (re.compile(r'(fc2?[_-]?ppv[_-]?\d{6,})', re.I), 'fc2_ppv_search'), # 如 fc2ppv-123456
            # 5. 【新增】无连字符但可识别为番号的格式：字母+数字+可选字母后缀
            # 如 JUQ996C -> JUQ-996, aka038FHD -> aka-038, mdtm360 -> mdtm-360
            # 要求：至少2个字母 + 至少2个数字 + 可选1个字母后缀
            (re.compile(r'([a-zA-Z]{2,})(\d{2,})([a-zA-Z]?)'), 'no_hyphen_with_suffix'),
        ]

    def extract_code(self, text: str) -> Optional[Dict]:
        """
        提取完整番号信息
        返回包含完整番号、字母部分、数字部分的字典
        """
        # 1. 首先尝试从开头匹配（原有逻辑，保持向后兼容）
        for pattern_regex, ptype in self.patterns:
            match = pattern_regex.match(text)
            if match:
                matched_text = match.group(1)
                full_code = matched_text.replace('-', '').replace('_', '').lower()
                letters = ''.join(re.findall(r'[a-zA-Z]+', full_code))
                numbers = ''.join(re.findall(r'\d+', full_code))

                return {
                    'full': full_code,           # 清理后的纯字母数字（用于内部匹配比较）
                    'original': matched_text,    # 原始匹配文本
                    'canonical': matched_text.replace('_', '-'),  # 标准化连字符格式（用于显示/文件名）
                    'letters': letters,
                    'numbers': numbers,
                    'type': ptype,
                    'is_short_numeric': False
                }

        # 2. 新增：如果从开头匹配失败，尝试在文本中搜索番号模式
        # 这可以处理如 "hhd800.com@MXGS-1397.mp4" 这样的情况
        for pattern_regex, ptype in self.search_patterns:
            # 使用 finditer 遍历所有匹配位置，避免域名遮挡后面的番号
            for match in pattern_regex.finditer(text):
                # 【新增】处理 no_hyphen_with_suffix 类型
                if ptype == 'no_hyphen_with_suffix':
                    letters = match.group(1)
                    numbers = match.group(2)
                    suffix = match.group(3)

                    # 过滤：跳过域名部分（如 kfa55.com 中的 kfa55）
                    end_pos = match.end()
                    domain_suffixes = ['com', 'net', 'org', 'cn', 'jp', 'tv', 'cc', 'info', 'biz']
                    skip = False

                    if end_pos < len(text) and text[end_pos] == '.':
                        # 检查 . 后面是否是域名后缀
                        after_dot = text[end_pos+1:end_pos+5].lower()
                        for ds in domain_suffixes:
                            if after_dot.startswith(ds):
                                # 域名后面还有 @ 或 / 等分隔符
                                next_char_pos = end_pos + 1 + len(ds)
                                if next_char_pos < len(text) and text[next_char_pos] in ['.', '@', '/']:
                                    skip = True
                                    break
                                # 或 . 后面全是短字母（域名特征）
                                remaining = text[end_pos+1:]
                                if remaining and all(c.isalpha() for c in remaining.split('.')[0]) and len(remaining.split('.')[0]) <= 4:
                                    skip = True
                                    break

                    # 过滤：跳过 @ 分隔符前面的部分
                    if end_pos < len(text) and text[end_pos] == '@':
                        skip = True

                    if skip:
                        continue  # 继续 finditer 的下一个匹配

                    # 过滤：字母部分太短
                    if len(letters) < 2:
                        continue

                    # 过滤：纯数字过长（可能是日期或其他数字）
                    if len(numbers) > 10:
                        continue

                    matched_text = f"{letters}-{numbers}"
                    full_code = letters.lower() + numbers

                    return {
                        'full': full_code,
                        'original': f"{letters}{numbers}{suffix}",
                        'canonical': matched_text,  # 标准化为带连字符格式
                        'letters': letters.lower(),
                        'numbers': numbers,
                        'type': ptype,
                        'is_short_numeric': False
                    }

                # 原有逻辑处理其他类型
                matched_text = match.group(1)
                full_code = matched_text.replace('-', '').replace('_', '').lower()
                letters = ''.join(re.findall(r'[a-zA-Z]+', full_code))
                numbers = ''.join(re.findall(r'\d+', full_code))

                # 过滤掉纯数字过长的情况（可能是日期或其他数字）
                if len(numbers) > 10:
                    continue

                # 过滤掉字母部分太短的情况（可能是单词的一部分）
                # 修改：纯数字番号格式（numeric_search）不需要字母部分
                if ptype != 'numeric_search' and len(letters) < 2:
                    continue

                return {
                    'full': full_code,
                    'original': matched_text,
                    'canonical': matched_text.replace('_', '-'),
                    'letters': letters,
                    'numbers': numbers,
                    'type': ptype,
                    'is_short_numeric': False
                }

        # 3. 通用提取（要求同时有字母和数字）- 作为最后备选
        letters = ''.join(re.findall(r'[a-zA-Z]+', text)).lower()
        numbers = ''.join(re.findall(r'\d+', text))

        if letters and numbers:
            full_code = letters + numbers
            original = text.split()[0] if text else text
            return {
                'full': full_code,
                'original': original,
                'canonical': original.replace('_', '-'),  # 标准化连字符格式
                'letters': letters,
                'numbers': numbers,
                'type': 'generic',
                'is_short_numeric': False
            }

        # 4. 纯数字短文件夹检查（长度<=3）
        if numbers and not letters and len(numbers) <= 3:
            return {
                'full': numbers,
                'original': text,
                'canonical': text.replace('_', '-'),
                'letters': '',
                'numbers': numbers,
                'type': 'short_numeric',
                'is_short_numeric': True
            }

        return None

    def normalize_number(self, number: str, target_length: int = 3) -> str:
        """规范化数字，处理前导零"""
        stripped = number.lstrip('0')
        if not stripped:
            return '0' * target_length
        if len(stripped) <= target_length:
            return stripped.zfill(target_length)
        return stripped[-target_length:]

    def is_strict_match(self, file_code: Dict, folder_code: Dict) -> int:
        """
        严格匹配检查
        返回：0=不匹配，1=弱匹配，2=强匹配
        """
        if not file_code or not folder_code:
            return 0

        # 短数字特殊处理：必须完全相等
        if file_code.get('is_short_numeric') or folder_code.get('is_short_numeric'):
            return 2 if file_code['full'] == folder_code['full'] else 0

        file_full = file_code['full']
        folder_full = folder_code['full']

        # 完全相等（清理后的full字段）
        if file_full == folder_full:
            return 2

        # 互相包含（处理可能的额外字符）
        if folder_full in file_full or file_full in folder_full:
            return 2

        # 字母和数字分别验证（用于弱匹配判断）
        file_letters = file_code['letters']
        folder_letters = folder_code['letters']
        file_numbers = file_code['numbers']
        folder_numbers = file_code['numbers']

        # 字母匹配：互相包含
        letters_match = (folder_letters in file_letters) or (file_letters in folder_letters)

        # 数字匹配：处理前导零和包含关系
        numbers_match = False
        if file_numbers and folder_numbers:
            file_num_stripped = file_numbers.lstrip('0') or '0'
            folder_num_stripped = folder_numbers.lstrip('0') or '0'

            if file_num_stripped == folder_num_stripped:
                numbers_match = True
            elif folder_num_stripped in file_num_stripped:
                numbers_match = True
            elif file_num_stripped in folder_num_stripped:
                numbers_match = True
            elif file_numbers.endswith(folder_numbers) or folder_numbers.endswith(file_numbers):
                numbers_match = True

        if letters_match and numbers_match:
            return 1

        return 0