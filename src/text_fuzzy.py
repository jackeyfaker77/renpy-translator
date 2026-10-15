"""原文模糊匹配（跨项目导入译文用）

分层（维护者定的度）：
- 归一化全等 或 ratio ≥ AUTO_THRESH → 自动并入（差异几乎必是空白/标记/
  变量名/标点——归一化想吸收的东西）
- AUTO_THRESH ~ MIN_THRESH → 「疑似匹配」，逐条人工筛选
- < MIN_THRESH → 不匹配，不打扰

归一化吸收跨版本最常见的差异源：{标记}、[变量]/$变量（两边变量名不同）、
空白、引号/省略号样式。短句（归一化后 < 10 字符）只认归一化全等——
"Yes." 这类短句相似度虚高，极易误配。

纯标准库（difflib），倒排词表预筛候选避免全量两两比较。
"""
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

AUTO_THRESH = 0.95   # ≥ 自动匹配
MIN_THRESH = 0.75    # ≥ 进疑似组人工筛选；< 不匹配
SHORT_LINE_CHARS = 10
# 候选生成与两级评分（性能关键，勿删）：
# 无上限的倒排合并 + 逐候选 SequenceMatcher 是 O(n×m)（实测 400×400 要
# 30 秒，真实项目规模直接卡死导入预览）。单次成本标定：词面 Jaccard ≈4µs、
# quick_ratio ≈18µs、ratio ≈208µs。所以：
#   1) 块键只用稀有词（df ≤ 5% 且绝对 ≤200），长 posting 会淹没候选池；
#   2) 候选按最稀有词优先凑，最多 MAX_CANDIDATES 个；
#   3) 先 Jaccard 快筛（真近重复必然共享多数实词），过了才做字符级比对。
INDEX_MAX_DF_FRAC = 0.05
INDEX_MAX_DF = 200
MAX_CANDIDATES = 40
JACCARD_PREFILTER = 0.3

_TAG_RE = re.compile(r'\{[^}]*\}')
_VAR_BRACKET_RE = re.compile(r'\[[^\]]*\]')
_VAR_DOLLAR_RE = re.compile(r'\$[A-Za-z_]\w*')
_WS_RE = re.compile(r'\s+')
_QUOTE_MAP = str.maketrans({
    '“': '"', '”': '"', '‘': "'", '’': "'",
    '„': '"', '―': '-', '–': '-',
})
_ELLIPSIS_RE = re.compile(r'\.{2,}')
_TOKEN_RE = re.compile(r"[a-z0-9']+")
# 块键用的停用词（太常见没有区分度）
_STOPWORDS = frozenset(
    'the a an and or but to of in on at is are was were be been am i you he she '
    'it we they them his her its my your our their this that these those with '
    'for as not no do does did have has had if then so what who whom which when '
    'where why how all any both each few more most other some such only own '
    'same than too very can will just don should now'.split())


def normalize_original(text: str) -> str:
    """归一化原文用于匹配比较（不改库里的原文本身）"""
    s = (text or '').translate(_QUOTE_MAP)
    s = s.replace('…', '...')
    s = _ELLIPSIS_RE.sub('...', s)         # .. / .... 都归一成 ...
    s = _TAG_RE.sub('', s)                 # {i}{/i}{size=-2} 等标记
    s = _VAR_BRACKET_RE.sub(' V ', s)      # [player_name] → 占位
    s = _VAR_DOLLAR_RE.sub(' V ', s)       # $mc_name → 占位
    s = s.lower()
    s = _WS_RE.sub(' ', s).strip()
    return s


def similarity(a: str, b: str) -> float:
    """归一化文本的相似度（0~1）"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _tokens(norm: str) -> set:
    return set(_TOKEN_RE.findall(norm)) - _STOPWORDS


def match_texts(target_items, source_items,
                auto_thresh=AUTO_THRESH, min_thresh=MIN_THRESH) -> dict:
    """把目标条目与源条目按原文匹配，返回分层结果

    target_items / source_items: [{'key': ..., 'original': ..., 'translated': ...}]
    返回：
      auto:   [{target_key, source_key, score, kind:'exact'|'normalized'|'fuzzy'}]
      fuzzy:  同结构（score 在 [min_thresh, auto_thresh)，人工筛选区）
      unmatched: 未匹配的目标 key 数
    同 key 多个源匹配时取 score 最高；同原文多条目标各自匹配。
    """
    src_norm = [(it, normalize_original(it.get('original', '')))
                for it in source_items]
    src_tokens = [_tokens(norm) for _it, norm in src_norm]
    by_norm = defaultdict(list)
    for idx, (it, norm) in enumerate(src_norm):
        if norm:
            by_norm[norm].append(idx)

    # 倒排索引（块键）：只用稀有词做键（高频词没有区分度，反而把候选池撑爆）
    n_src = len(src_norm) or 1
    df = Counter()
    for toks in src_tokens:
        df.update(toks)
    df_cap = min(INDEX_MAX_DF, max(2, int(n_src * INDEX_MAX_DF_FRAC)))
    index = defaultdict(list)
    for idx, toks in enumerate(src_tokens):
        for w in toks:
            if 1 <= df[w] <= df_cap:
                index[w].append(idx)

    auto, fuzzy, unmatched = [], [], 0
    for tit in target_items:
        tkey = tit.get('key')
        tnorm = normalize_original(tit.get('original', ''))
        if not tnorm:
            unmatched += 1
            continue
        best = None  # (score, idx, kind)

        # 1) 归一化全等（含原文全等——原文全等必然归一化全等）
        if tnorm in by_norm:
            best = (1.0, by_norm[tnorm][0], 'exact'
                    if src_norm[by_norm[tnorm][0]][0].get('original')
                       == tit.get('original') else 'normalized')
        # 2) 短句保护：只认归一化全等
        elif len(tnorm) >= SHORT_LINE_CHARS:
            ttoks = _tokens(tnorm)
            # 候选从最稀有的词凑（真近重复共享稀有词，排在最前），凑满即止
            cand, seen = [], set()
            for w in sorted(ttoks, key=lambda x: df.get(x, 0)):
                if not index.get(w):
                    continue
                for idx in index[w]:
                    if idx not in seen:
                        seen.add(idx)
                        cand.append(idx)
                        if len(cand) >= MAX_CANDIDATES:
                            break
                if len(cand) >= MAX_CANDIDATES:
                    break
            best_score, best_idx = 0.0, -1
            for idx in cand:
                stoks = src_tokens[idx]
                # 快筛：词面交集不够的不可能到字符级阈值
                inter = len(ttoks & stoks)
                if inter / max(1, len(ttoks | stoks)) < JACCARD_PREFILTER:
                    continue
                other = src_norm[idx][1]
                la, lb = len(tnorm), len(other)
                if min(la, lb) / max(la, lb) < 0.6:
                    continue  # 长度差太多的不可能到阈值
                sm = SequenceMatcher(None, tnorm, other)
                if sm.quick_ratio() < min_thresh:
                    continue  # 上界不够高，跳过全量比对
                score = sm.ratio()
                if score > best_score:
                    best_score, best_idx = score, idx
            if best_idx >= 0 and best_score >= min_thresh:
                best = (best_score, best_idx, 'fuzzy')

        if best is None:
            unmatched += 1
            continue
        score, idx, kind = best
        row = {'target_key': tkey,
               'source_key': src_norm[idx][0].get('key'),
               'source_original': src_norm[idx][0].get('original', ''),
               'source_translated': src_norm[idx][0].get('translated', ''),
               'target_translated': tit.get('translated', ''),
               'score': round(score, 4), 'kind': kind}
        if score >= auto_thresh:
            auto.append(row)
        else:
            fuzzy.append(row)

    fuzzy.sort(key=lambda r: -r['score'])
    return {'auto': auto, 'fuzzy': fuzzy, 'unmatched': unmatched}
