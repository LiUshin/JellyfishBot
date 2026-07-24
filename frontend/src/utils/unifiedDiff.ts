/**
 * Lightweight unified-diff with hunk slicing.
 *
 * 不依赖 jsdiff（~50KB gz）。流程：
 *   1. 把 oldText（编辑已落盘时则是 newText）在 originalText 中定位 → 算出片段的起止行
 *   2. 用旧片段 lines / 新片段 lines 做行级 LCS-like 对齐（与 ApprovalCard
 *      内的 computeLineDiff 同一思路），得到改动行序列
 *   3. 把改动行序列回填到原文件视图：旧片段范围内按对齐结果展开为
 *      `+`/`-`/` `（context）行；旧片段之外仍是原文（context）
 *   4. 按上下文行数（默认 3）切 hunk，相邻 hunk 重叠合并
 *
 * 输出：DiffHunk[]，每个 hunk 含 oldStart/oldLines、newStart/newLines、
 * 一组 DiffLine（type / oldNum / newNum / text）。
 */

export type DiffLineType = 'context' | 'add' | 'del';

export interface DiffLine {
  type: DiffLineType;
  oldNum: number | null;
  newNum: number | null;
  text: string;
}

export interface DiffHunk {
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  lines: DiffLine[];
}

export interface DiffResult {
  /** 修改后的整文件行数（用于「展开全文」视图分页判断） */
  totalNewLines: number;
  hunks: DiffHunk[];
  /** 失败原因：'not_found' = old_string / new_string 都没在原文里找到（agent 写错了 / 文件已被改） */
  error: 'not_found' | null;
  /** 原文件里已经是 new_string（编辑已落盘），diff 由还原 old_string 反推得到 */
  alreadyApplied: boolean;
  /** 展开全文用：完整的 DiffLine 序列（不切 hunk） */
  fullDiff: DiffLine[];
}

/** 统一换行，避免 CRLF 文件与 LLM 输出的 LF 片段匹配不上。 */
function normalizeEol(text: string): string {
  return text.replace(/\r\n/g, '\n');
}

/**
 * 把 newString 替换 originalText 中第一次出现的 oldString，再做行级 diff。
 *
 * 注意：deepagents 的 edit_file 内置语义就是「替换第一次出现」，这里和后端
 * 行为对齐（不做全局替换）。
 *
 * originalText 是「当前磁盘内容」，它可能处于编辑前或编辑后两种状态：
 * 待审批时含 oldString；编辑已落盘（YOLO / 批准后 / 历史回看）时含 newString。
 * 后者反过来把 newString 还原成 oldString，即可推出编辑前内容照常出 diff。
 */
export function computeUnifiedDiff(
  originalText: string,
  oldString: string,
  newString: string,
  contextLines = 3,
): DiffResult {
  const original = normalizeEol(originalText);
  const oldStr = normalizeEol(oldString);
  const newStr = normalizeEol(newString);

  let beforeText: string;
  let afterText: string;
  let alreadyApplied = false;

  const idx = original.indexOf(oldStr);
  if (idx >= 0) {
    beforeText = original;
    afterText = original.slice(0, idx) + newStr + original.slice(idx + oldStr.length);
  } else {
    // newStr 为空时 indexOf 恒为 0，会把「纯删除」误判成已落盘，必须先排除。
    const appliedIdx = newStr ? original.indexOf(newStr) : -1;
    if (appliedIdx < 0) {
      // 两个片段都不在文件里：agent 写错，或文件被第三方改过。
      // 退化策略：交给调用方显示双段对照（用户至少看到 new_string 是什么）
      return {
        totalNewLines: newStr.split('\n').length,
        hunks: [],
        error: 'not_found',
        alreadyApplied: false,
        fullDiff: [],
      };
    }
    alreadyApplied = true;
    beforeText = original.slice(0, appliedIdx) + oldStr + original.slice(appliedIdx + newStr.length);
    afterText = original;
  }

  const fullDiff = lineDiff(beforeText.split('\n'), afterText.split('\n'));
  const hunks = sliceHunks(fullDiff, contextLines);

  return {
    totalNewLines: afterText.split('\n').length,
    hunks,
    error: null,
    alreadyApplied,
    fullDiff,
  };
}

/**
 * 行级 LCS。返回带类型与新旧行号的 DiffLine 序列。
 *
 * 实现：经典 DP（O(N*M) 内存/时间）。文件中等大小（< 几千行）足够；
 * 极大文件 fallback 到「窗口对齐」可在未来按需加。
 */
export function lineDiff(oldLines: string[], newLines: string[]): DiffLine[] {
  const n = oldLines.length;
  const m = newLines.length;

  // dp[i][j] = LCS length of oldLines[0..i) vs newLines[0..j)
  // 用一维滚动数组省内存
  const prev = new Int32Array(m + 1);
  const curr = new Int32Array(m + 1);
  // 还原路径需要完整 dp：内存换简洁。文件大小通常 < 5000 行，dp 占用可控。
  const dp: Int32Array[] = [new Int32Array(m + 1)];
  for (let i = 1; i <= n; i++) {
    const row = new Int32Array(m + 1);
    for (let j = 1; j <= m; j++) {
      if (oldLines[i - 1] === newLines[j - 1]) {
        row[j] = dp[i - 1][j - 1] + 1;
      } else {
        row[j] = Math.max(dp[i - 1][j], row[j - 1]);
      }
    }
    dp.push(row);
  }
  // suppress unused: prev/curr only kept for future memory-tight version
  void prev; void curr;

  // 回溯
  const out: DiffLine[] = [];
  let i = n;
  let j = m;
  while (i > 0 && j > 0) {
    if (oldLines[i - 1] === newLines[j - 1]) {
      out.push({ type: 'context', oldNum: i, newNum: j, text: oldLines[i - 1] });
      i--; j--;
    } else if (dp[i - 1][j] > dp[i][j - 1]) {
      // 严格大于：保证回溯优先走 add 分支（j--），reverse 后输出
      // 「del 先于 add」的 git 习惯顺序。
      out.push({ type: 'del', oldNum: i, newNum: null, text: oldLines[i - 1] });
      i--;
    } else {
      out.push({ type: 'add', oldNum: null, newNum: j, text: newLines[j - 1] });
      j--;
    }
  }
  while (i > 0) {
    out.push({ type: 'del', oldNum: i, newNum: null, text: oldLines[i - 1] });
    i--;
  }
  while (j > 0) {
    out.push({ type: 'add', oldNum: null, newNum: j, text: newLines[j - 1] });
    j--;
  }
  out.reverse();
  return out;
}

/**
 * 按上下文行数切 hunk，相邻 hunk 重叠时合并。
 *
 * 算法：
 *   - 找出所有「改动行（add/del）」的索引 i
 *   - 对每个改动行，标记 [i - ctx, i + ctx] 范围内的行号为 keep
 *   - keep 范围连成 hunk
 */
export function sliceHunks(full: DiffLine[], contextLines: number): DiffHunk[] {
  if (full.length === 0) return [];

  const keep = new Uint8Array(full.length);
  for (let i = 0; i < full.length; i++) {
    if (full[i].type !== 'context') {
      const lo = Math.max(0, i - contextLines);
      const hi = Math.min(full.length - 1, i + contextLines);
      for (let k = lo; k <= hi; k++) keep[k] = 1;
    }
  }

  const hunks: DiffHunk[] = [];
  let i = 0;
  while (i < full.length) {
    if (!keep[i]) { i++; continue; }
    const start = i;
    while (i < full.length && keep[i]) i++;
    const end = i; // exclusive

    const slice = full.slice(start, end);
    // 计算 hunk 头：第一个有 oldNum / newNum 的行
    const firstOldNum = slice.find(l => l.oldNum != null)?.oldNum ?? 1;
    const firstNewNum = slice.find(l => l.newNum != null)?.newNum ?? 1;
    const oldCount = slice.filter(l => l.type !== 'add').length;
    const newCount = slice.filter(l => l.type !== 'del').length;

    hunks.push({
      oldStart: firstOldNum,
      oldLines: oldCount,
      newStart: firstNewNum,
      newLines: newCount,
      lines: slice,
    });
  }
  return hunks;
}
