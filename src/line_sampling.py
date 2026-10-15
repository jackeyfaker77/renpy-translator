"""零 LLM 台词抽样与风格信号预筛（人物分析用）

移植自 DSH 调研原型 demo_sampling.py（合成语料对照实验见
F:/dsh/research/character_voice_sampling/results.md）：

- 均匀抽 300 句对 0.5% 频率口癖的召回只有 ~17%；
- 「稀缺口癖配额 + 通用启发式 + 探索槽」的显式预算组合能到 ~80%；
- 纯多样性 coreset/聚类对口癖几乎无效（27% vs 启发式 44%），
  因此这里不做 embedding/聚类，全部零依赖启发式；
- 探索槽不是浪费预算：它是唯一能发现「没想到的口癖」的机制。

输出两样东西：
1. selected：预算内台词子集（槽位组合选句），按 core/support 分两组
   供两阶段 LLM 分析（core 先出草稿，support 定向补样）；
2. style_signals：候选口癖/称谓/标点习惯（注入分析提示词，
   让 LLM 知道该盯哪些词——不动抽样就能提升分析质量）。

line 字典形状：至少含 original_text；scene 取 label（空则整料算一幕）。
"""
import math
import random
import re
from collections import Counter, defaultdict

TOKEN_RE = re.compile(r"[a-z']+")
PUNCT_TOKENS = ("!", "...", "?", "--")
# 称谓/呼语启发式（英文原文）：my X、句首 Name, 两类覆盖大部分呼语形态
ADDR_MY_RE = re.compile(r"\bmy (lord|lady|love|boy|dear|friend|captain|king|queen)\b")
ADDR_NAME_RE = re.compile(r"^[A-Z][a-z]+[,!]")
ELLIPSIS_END_RE = re.compile(r"(\.\.\.|…)\s*[\"'”’)]?\s*$")

# 槽位预算占比（调研 sweep：口癖配额 25% 时已知信号与 held-out 双优）
SCENE_FRAC = 0.20      # S1 剧情覆盖：每幕硬保底
RARE_FRAC = 0.25       # S3 稀缺口癖配额
GENERIC_FRAC = 0.35    # S4 通用启发式（标点/短句/称谓打分 top）
EXPLORE_FRAC = 0.10    # S5 探索槽（均匀随机，捞未知信号）
# 剩余约 10% 由 MMR 冗余惩罚填充（P4）

# 口癖候选口径：自己台词里足够稀有（低 DF）且相对语料过度使用（z 高）
TIC_MAX_DF_FRAC = 0.08
TIC_MIN_COUNT = 3
TIC_MIN_Z = 1.0
TIC_PER_WORD_QUOTA = 4

SHORT_LINE_CHARS = 10


def tokenize(text: str) -> list:
    return TOKEN_RE.findall((text or '').lower())


def line_text(line) -> str:
    return (line.get('original_text') if isinstance(line, dict) else line) or ''


def line_scene(line) -> str:
    return (line.get('label') or '') if isinstance(line, dict) else ''


def corpus_background(all_lines) -> Counter:
    """全语料词频背景（含目标角色自身——log-odds 的对照基线）"""
    bg = Counter()
    for ln in all_lines:
        bg.update(tokenize(line_text(ln)))
    return bg


def log_odds_distinctiveness(lines, bg, prior=0.05) -> dict:
    """Monroe et al. 2008：带信息性 Dirichlet 先验的 log-odds z 分数

    「这个词是不是这个角色过度使用的」——口癖检测的核心信号。
    """
    c = Counter()
    for ln in lines:
        c.update(tokenize(line_text(ln)))
    n_c, n_b = sum(c.values()), sum(bg.values())
    if not n_c or not n_b:
        return {}
    scores = {}
    for w, cnt in c.items():
        if cnt < TIC_MIN_COUNT:
            continue
        a = cnt + prior
        b = bg.get(w, 0) + prior
        p_c = a / (n_c + prior * (len(c) + 1))
        p_b = b / (n_b + prior * (len(bg) + 1))
        if p_c >= 1 or p_b >= 1 or p_b <= 0:
            continue
        lo = math.log(p_c / (1 - p_c)) - math.log(p_b / (1 - p_b))
        var = 1 / a + 1 / b
        scores[w] = lo / math.sqrt(var)
    return scores


def rare_distinctive_words(word_z, lines, max_df_frac=TIC_MAX_DF_FRAC,
                           min_count=TIC_MIN_COUNT, bonus=2.5) -> dict:
    """口癖候选 = 过度使用（高 z）× 自己台词里也稀有（低 DF）

    用 DF 不用 TF：出现在三分之一台词里的词是语言习惯不是角色指纹，
    不需要「抢救」；均匀抽样几乎碰不到两遍的正是低 DF 高 z 的词。
    """
    cnt, df = Counter(), Counter()
    for ln in lines:
        toks = tokenize(line_text(ln))
        cnt.update(toks)
        df.update(set(toks))
    n_lines = len(lines) or 1
    cands = {}
    for w, c in cnt.items():
        if c < min_count or df[w] / n_lines > max_df_frac:
            continue
        if word_z.get(w, 0) < TIC_MIN_Z:
            continue
        cands[w] = word_z[w] * bonus
    return cands


def dedup_lines(lines, sim_thresh=0.85) -> list:
    """精确 + 近似去重（token 集 Jaccard）。模板句（'Yes.'/'Very well.'）
    在真实剧本里大量重复，不去重会吃掉预算。近似比较用倒排索引做块键，
    只比共享中低频词的对，避免全量两两比较。"""
    seen, exact_kept = set(), []
    for ln in lines:
        key = line_text(ln).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        exact_kept.append(ln)

    token_sets = [set(tokenize(line_text(ln))) for ln in exact_kept]
    # 块键：只用出现于 1~30% 行的词（全库高频词做键没有区分度）
    n = len(exact_kept) or 1
    df = Counter()
    for s in token_sets:
        df.update(s)
    index = defaultdict(list)
    for i, s in enumerate(token_sets):
        for w in s:
            if 1 <= df[w] <= max(2, int(n * 0.30)):
                index[w].append(i)

    kept_sets = {}
    kept_idx = []
    for i in sorted(range(len(exact_kept)),
                    key=lambda j: -len(line_text(exact_kept[j]))):
        s = token_sets[i]
        cand = set()
        for w in s:
            for j in index.get(w, ()):
                if j in kept_sets:
                    cand.add(j)
        dup = False
        for j in cand:
            k = kept_sets[j]
            if k and len(s & k) / max(1, len(s | k)) > sim_thresh:
                dup = True
                break
        if not dup:
            kept_idx.append(i)
            kept_sets[i] = s
    return [exact_kept[i] for i in kept_idx]


def score_lines(lines, word_z, rare_cands=None) -> list:
    """通用启发式打分（零 LLM）：特异词 / 稀有复现词 / 标点习惯 / 短促句 / 称谓"""
    rare_cands = rare_cands or {}
    freq = Counter()
    for ln in lines:
        freq.update(tokenize(line_text(ln)))
    total = sum(freq.values()) or 1
    scored = []
    for ln in lines:
        text = line_text(ln)
        toks = tokenize(text)
        if len(toks) < 3:
            continue  # 极短句/纯标记句：信息量不足，不占预算
        dist = sum(max(0.0, word_z.get(w, 0.0)) for w in set(toks))
        dist /= math.sqrt(len(toks))
        idio = sum(1.0 for w, c in Counter(toks).items()
                   if 2 <= freq[w] <= 0.03 * total) / math.sqrt(len(toks))
        punct = sum(1.2 for p in PUNCT_TOKENS if p in text.lower())
        short = 1.0 if len(toks) <= 6 else 0.0
        addr = 0.0
        low = text.lower()
        if ADDR_MY_RE.search(low):
            addr += 1.5
        if ADDR_NAME_RE.match(text):
            addr += 1.0
        rare = sum(rare_cands.get(w, 0.0) for w in set(toks))
        scored.append((ln, dist + 0.8 * idio + 0.35 * punct + 0.25 * short
                       + 0.5 * addr + rare))
    return scored


def evidence_flags(lines, tic_scenes: dict) -> dict:
    """证据充分性门控（Eder 2010：<3000 词的风格统计不可靠）

    tic_scenes: {口癖词: set(scene)}，仅 1 幕的口癖多半是场景用词，
    档案里要标注存疑而不是当角色指纹。
    """
    n_words = sum(len(tokenize(line_text(ln))) for ln in lines)
    single_scene = sorted(w for w, sc in tic_scenes.items() if len(sc) < 2)
    return {
        'words': n_words,
        'low_evidence': n_words < 3000,
        'single_scene_tics': single_scene,
    }


def portfolio_select(scored, budget, n_scenes, rare_cands=None,
                     rng=None, per_word_quota=TIC_PER_WORD_QUOTA):
    """显式槽位预算组合（核心）：槽位独立切分，不做单一加权分。

    返回 (core, support)：core = 口癖配额 + 通用启发式（风格证据主力，
    草稿分析用）；support = 剧情覆盖 + 探索 + 填充（定向补样用）。
    """
    rng = rng or random.Random(0)
    rare_cands = rare_cands or {}
    n_scenes = max(1, n_scenes)
    n_rare = max(1, int(budget * RARE_FRAC))
    n_generic = max(1, int(budget * GENERIC_FRAC))
    n_explore = max(1, int(budget * EXPLORE_FRAC))
    per_scene_floor = max(1, int(budget * SCENE_FRAC) // n_scenes)

    by_scene = defaultdict(list)
    for ln, s in scored:
        by_scene[line_scene(ln)].append((ln, s))

    chosen, chosen_ids, slot_of = [], set(), {}
    seen_words = Counter()

    def take(ln, slot):
        if id(ln) in chosen_ids or len(chosen) >= budget:
            return False
        chosen.append(ln)
        chosen_ids.add(id(ln))
        slot_of[id(ln)] = slot
        seen_words.update(set(tokenize(line_text(ln))))
        return True

    # S1 剧情覆盖：每幕保底（分数最高的代表句）
    for sc in sorted(by_scene):
        pool = sorted(by_scene[sc], key=lambda x: -x[1])
        for ln, _s in pool[:per_scene_floor]:
            take(ln, 'scene')

    # S3 稀缺口癖配额：每个候选词至少几句，优先语境完整的长句
    used_rare = 0
    for w, _z in sorted(rare_cands.items(), key=lambda kv: -kv[1]):
        if used_rare >= n_rare or len(chosen) >= budget:
            break
        pat = re.compile(r"\b%s\b" % re.escape(w))
        have = sum(1 for ln in chosen if pat.search(line_text(ln).lower()))
        pool = [ln for ln, _s in scored if pat.search(line_text(ln).lower())]
        pool.sort(key=lambda ln: -len(line_text(ln)))
        for ln in pool:
            if have >= per_word_quota or used_rare >= n_rare or len(chosen) >= budget:
                break
            if take(ln, 'rare'):
                have += 1
                used_rare += 1

    # S4 通用启发式：打分最高的句子（已选过的跳过）
    added = 0
    for ln, _s in sorted(scored, key=lambda x: -x[1]):
        if added >= n_generic or len(chosen) >= budget:
            break
        if take(ln, 'generic'):
            added += 1

    # S5 探索槽：剩余池均匀随机（唯一能捞「没想到的口癖」的机制）
    pool = [ln for ln, _s in scored if id(ln) not in chosen_ids]
    if pool:
        for ln in rng.sample(pool, min(n_explore, len(pool))):
            take(ln, 'explore')

    # P4 填充：窗口化 MMR 贪心（分数 - 冗余惩罚，防同句式扎堆）
    rest = [x for x in scored if id(x[0]) not in chosen_ids]
    rest.sort(key=lambda x: -x[1])
    while len(chosen) < budget and rest:
        best, best_v = None, -1e9
        for ln, s in rest[:400]:
            toks = set(tokenize(line_text(ln)))
            red = sum(seen_words[w] for w in toks) / max(1, len(toks))
            v = s - 0.15 * red
            if v > best_v:
                best, best_v = (ln, s), v
        if best is None:
            break
        take(best[0], 'fill')
        rest = [x for x in rest if id(x[0]) not in chosen_ids]

    core = [ln for ln in chosen if slot_of[id(ln)] in ('rare', 'generic')]
    support = [ln for ln in chosen if slot_of[id(ln)] in ('scene', 'explore', 'fill')]
    return core, support


def _collect_style_signals(lines, rare_cands, tic_scenes) -> dict:
    """分析提示词注入用：候选口癖 / 称谓命中 / 标点习惯"""
    tics = []
    cnt = Counter()
    for ln in lines:
        cnt.update(tokenize(line_text(ln)))
    for w in sorted(rare_cands, key=lambda x: -rare_cands[x])[:12]:
        tics.append({'word': w, 'count': cnt.get(w, 0),
                     'scenes': len(tic_scenes.get(w, ()))})

    addr_hits = Counter()
    punct = Counter()
    for ln in lines:
        text = line_text(ln)
        low = text.lower()
        for m in ADDR_MY_RE.finditer(low):
            addr_hits[m.group(0)] += 1
        if ADDR_NAME_RE.match(text):
            addr_hits['<Name>, / <Name>!（句首呼名）'] += 1
        if ELLIPSIS_END_RE.search(text):
            punct['省略号结尾'] += 1
        if text.rstrip().endswith('!'):
            punct['感叹号结尾'] += 1
        if text.rstrip().endswith('?'):
            punct['问号结尾'] += 1
    return {
        'tics': tics,
        'addresses': [{'form': k, 'count': v}
                      for k, v in addr_hits.most_common(8)],
        'punctuation': [{'form': k, 'count': v}
                        for k, v in punct.most_common(5)],
    }


def sample_for_analysis(char_lines, all_lines, budget=300, rng=None) -> dict:
    """人物分析抽样总入口（纯函数，零 LLM，秒级）

    char_lines: 目标角色台词（dict，含 original_text / label）
    all_lines:  全项目台词（log-odds 背景基线）
    返回 {selected, core, support, signals, evidence, stats}
    """
    rng = rng or random.Random(0)
    lines = dedup_lines(char_lines)
    bg = corpus_background(all_lines)
    word_z = log_odds_distinctiveness(lines, bg)
    rare_cands = rare_distinctive_words(word_z, lines)

    tic_scenes = defaultdict(set)
    for ln in lines:
        toks = set(tokenize(line_text(ln)))
        for w in rare_cands:
            if w in toks:
                tic_scenes[w].add(line_scene(ln))

    scored = score_lines(lines, word_z, rare_cands)
    n_scenes = len({line_scene(ln) for ln in lines})
    if len(scored) <= budget:
        # 小角色：全量就是最好的样本（不值得再抽）
        core = [ln for ln, _s in scored]
        support = []
        evidence = evidence_flags(lines, dict(tic_scenes))
        return {
            'selected': core, 'core': core, 'support': support,
            'signals': _collect_style_signals(lines, rare_cands, dict(tic_scenes)),
            'evidence': evidence,
            'stats': {'total': len(char_lines), 'after_dedup': len(lines),
                      'selected': len(core), 'budget': budget, 'capped': False},
        }

    core, support = portfolio_select(scored, budget, n_scenes,
                                     rare_cands=rare_cands, rng=rng)
    evidence = evidence_flags(lines, dict(tic_scenes))
    return {
        'selected': core + support, 'core': core, 'support': support,
        'signals': _collect_style_signals(lines, rare_cands, dict(tic_scenes)),
        'evidence': evidence,
        'stats': {'total': len(char_lines), 'after_dedup': len(lines),
                  'selected': len(core) + len(support), 'budget': budget,
                  'capped': True},
    }
