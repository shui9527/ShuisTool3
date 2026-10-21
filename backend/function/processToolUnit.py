# -*- coding: utf-8 -*-
"""
ProcessToolUnit：字符串解析工具（重写版）

修复项：
  B11 capture_brackets 用 rfind 定位错误/多括号只能取第一个
      split_music_danmu 中 slist[1] 无保护（只有命令词时 IndexError）
"""
import re


class ProcssTool:
    def __init__(self):
        pass

    # ------------------------------------------------------------------
    def contains_brackets(self, s):
        return bool(re.search(r'[()（）]', s))

    def capture_brackets(self, s):
        """捕获第一对完整括号（半角/全角）中的内容；无匹配返回 None"""
        match = re.search(r'[（(](.*?)[)）]', s)
        return match.group(1) if match else None

    # ------------------------------------------------------------------
    def contains_quotes(self, s):
        return bool(re.search(r'["\'“”‘’]', s))

    def capture_quotes(self, s):
        """捕获第一对引号中的内容（半角/全角/中文）"""
        match = re.search(r'["“”‘’\'"]\s*(.*?)\s*["“”‘’\'"]', s)
        return match.group(1) if match else None

    # ------------------------------------------------------------------
    def contains_english(self, s):
        """是否含 ASCII 字母（用于英文歌名整段合并判定；命令词已在调用侧排除）"""
        return bool(re.search(r'[a-zA-Z]', str(s)))

    def upper_english(self, s):
        return s.upper()

    def remove_spaces(self, s):
        return s.strip()

    # ------------------------------------------------------------------
    def split_music_danmu(self, s):
        """
        切分点歌弹幕，返回 (歌曲名, 歌手名 或 None)
        解析优先级（自上而下）：
          1. 括号：点歌 (歌名) 歌手 / 点歌 （歌名） 歌手
             取第一对括号内为歌名，右括号后剩余为歌手
          2. 引号：点歌 "歌名" 歌手 / 点歌 “歌名” 歌手
             取第一对引号内为歌名
          3. 含 - 分隔符（半角 - / 全角 －、—，两侧允许空格）：
             按最后一个 - 切分，左侧整体为歌名、右侧整体为歌手（两侧 strip，内部空格原样保留）
             点歌 hotel california - eagles  -> 歌名=hotel california, 歌手=eagles
             点歌 晴天 - 周杰伦              -> 歌名=晴天, 歌手=周杰伦
          4. 空格兜底：
             - 含英文（ASCII 字母，命令词已排除）-> 命令词后全部合并为歌名，歌手为空
               点歌 hotel california -> 歌名=hotel california
             - 纯中文 -> 旧规则：命令 歌名 [歌手]，多余段忽略
        """
        s = self.remove_spaces(s)
        if not s:
            return None, None

        if self.contains_brackets(s):
            song_name = self.capture_brackets(s)
            if song_name is None:
                return None, None
            singer = None
            if '）' in s:
                singer = s.split('）')[-1].strip()
            elif ')' in s:
                singer = s.split(')')[-1].strip()
            return song_name.strip(), (singer or None)

        if self.contains_quotes(s):
            song_name = self.capture_quotes(s)
            if song_name is None:
                return None, None
            singer = None
            for quote_char in ('”', '"', '’', "'"):
                if quote_char in s:
                    singer = s.split(quote_char)[-1].strip()
                    if singer:
                        break
            return song_name.strip(), (singer or None)

        # 空格分隔：命令 歌曲 [歌手]
        # M5 修复：re.split(r'\s+') 兼容全角空格/制表符/连续空白，并过滤空串
        slist = [x for x in re.split(r'\s+', s) if x]
        # B11 只有命令词（如"点歌"）时，slist[1] 越界保护
        if len(slist) < 2:
            return None, None

        # 去掉命令词前缀后的剩余文本（strip 后保留内部空格原始形态）
        rest = s[len(slist[0]):].strip()
        if not rest:
            return None, None

        # 3. 含 - 分隔符：按最后一个 -（半角 - / 全角 －、—）切分
        #    左侧整体 = 歌名，右侧整体 = 歌手；两侧均非空才采用
        sep_hits = [m for m in re.finditer(r'[—－-]', rest)]
        if sep_hits:
            m = sep_hits[-1]
            left = rest[:m.start()].strip()
            right = rest[m.end():].strip()
            if left and right:
                return left, right

        # 4. 空格兜底：含英文 -> 命令词后全部合并为歌名，歌手为空（修复英文歌名空格截断）
        if self.contains_english(rest):
            return rest, None

        # 纯中文 -> 旧规则：命令 歌名 [歌手]，多余段忽略（保持老观众习惯）
        song_name = slist[1].strip()
        singer = slist[2].strip() if len(slist) >= 3 and slist[2].strip() else None
        return song_name, singer
