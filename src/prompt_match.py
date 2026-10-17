"""按批内容匹配注入：术语表/人名表不再全量注入

术语表与人名表按「本批待译原文 + 前文上下文」匹配后注入，本批用不到
的条目不占 token。匹配带合理模糊（带合理的模糊）：

1. 词面命中：条目英文的全部词元出现在本批词元集合（大小写不敏感）；
2. 边界包含：归一化后的条目短语以词边界形式出现在归一化批文本里
   （能吃掉复数/时态后缀：mate 命中 mates，但不误伤 teammate）；
3. 短条目（归一化后 <3 字符）只认词面命中，防误配。

纯函数、零依赖（复用 text_fuzzy 的归一化与 line_sampling 的分词）。
"""
import re

from line_sampling import tokenize
from text_fuzzy import normalize_original

SHORT_ENTRY_CHARS = 3

_BOUNDARY_RE_CACHE: dict = {}


def _boundary_re(phrase: str):
    """边界包含：词前界 + 简单词尾容忍（s/es/d/ed/ing）。

    mate 命中 mates/mated，但不命中 teammate（前界）或 matel（后界）——
    「合理模糊」= 形态变化，不是任意子串。
    """
    pat = _BOUNDARY_RE_CACHE.get(phrase)
    if pat is None:
        pat = re.compile(
            r'(?<![a-z0-9])' + re.escape(phrase) + r'(?:s|es|d|ed|ing)?(?![a-z0-9])')
        _BOUNDARY_RE_CACHE[phrase] = pat
    return pat


def match_entries(batch_texts, entries) -> list:
    """entries: [{'key':…, 'en':…}, …]；返回命中的原 dict 列表（保序）。

    batch_texts: 本批待译原文 + 前文上下文原文（str 列表，允许空串）。
    """
    if not entries:
        return []
    texts = [t for t in batch_texts if t]
    if not texts:
        return []
    hay = ' '.join(normalize_original(t) for t in texts)
    words: set = set()
    for t in texts:
        words.update(tokenize(t))
    hits = []
    for entry in entries:
        en = entry.get('en') or ''
        en_norm = normalize_original(en)
        if not en_norm:
            continue
        en_words = [w for w in tokenize(en) if w]
        exact = bool(en_words) and all(w in words for w in en_words)
        fuzzy = False
        if not exact and len(en_norm) >= SHORT_ENTRY_CHARS:
            fuzzy = _boundary_re(en_norm).search(hay) is not None
        if exact or fuzzy:
            hits.append(entry)
    return hits
