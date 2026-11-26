/**
 * 勾選多台一次納管的排程（從 BatchOnboardModal.vue 抽出來，才測得到）。
 *
 * 2026-10-19 使用者（公司機）：「失敗會卡住」。一次納管 7 台，前 4 台登入被拒，設計上會自動停下
 * （避免拿錯的密碼一直打、把帳號鎖住），但剩下 3 台一直寫「等待中」、計數寫「等待 3」——看起來像當掉。
 *
 * 這支保證：
 *   1. 跑完（含自動停止、某台丟例外）之後，**沒有任何一列還是 waiting／running**：
 *      沒試的標 stopped（未執行（已停止）），丟例外的標 failed
 *   2. 回傳停在哪：被拒幾台、沒試幾台——畫面講得出「為什麼停、剩幾台」
 */
export type QState = 'waiting' | 'running' | 'ok' | 'failed' | 'skipped' | 'stopped'
export interface QRow { state: QState; message: string; stage?: string }

/** 帳密被拒（後端 onboard_engine 的「登入被拒——帳號或密碼不對…」、onboard_failures 的同一組字） */
export const AUTH_FAIL_RE = /登入被拒|帳號或密碼|帳密|認證|密碼|Permission denied|authentication|auth/i

export function isAuthFail(r: QRow): boolean {
  return r.state === 'failed' && AUTH_FAIL_RE.test(r.message || '')
}

export interface QueueResult<T> {
  /** 是否因「一開始就帳密被拒」自動停下 */
  autoStopped: boolean
  /** 這一輪帳密被拒的台數 */
  authFailed: number
  /** 沒試到的（已標 stopped） */
  notRun: T[]
}

/**
 * 依序（最多 concurrency 台同時）跑 queue。`one` 自己負責把 row 改成 ok／failed／skipped；
 * 它丟例外也不會讓整輪卡住（那一台標 failed）。
 * 自動停止：這一輪還沒有任何一台成功、就出現帳密被拒 → 不再開始新的一台（已經在跑的跑完）。
 */
export async function runQueue<T extends QRow>(
  rows: T[], one: (r: T) => Promise<void>, concurrency: number,
): Promise<QueueResult<T>> {
  const queue = [...rows]
  queue.forEach((r) => { r.state = 'waiting'; r.message = ''; if ('stage' in r) r.stage = '' })
  let stop = false
  let okCount = 0
  async function worker() {
    while (queue.length && !stop) {
      const r = queue.shift()!
      r.state = 'running'
      try {
        await one(r)
      } catch (err: any) {
        r.state = 'failed'
        r.message = err?.data?.detail ?? err?.message ?? '請稍後重試'
      }
      if (r.state === 'running') { r.state = 'failed'; r.message = r.message || '沒有回應' }
      if (r.state === 'ok') okCount++
      if (isAuthFail(r) && okCount === 0) stop = true
    }
  }
  await Promise.allSettled(Array.from({ length: Math.max(1, Math.min(concurrency, queue.length)) }, worker))
  const notRun = rows.filter((r) => r.state === 'waiting' || r.state === 'running')
  for (const r of notRun) {
    r.state = 'stopped'
    r.message = '未執行：前面幾台帳密被拒，已自動停止（避免帳號被鎖）'
  }
  return { autoStopped: stop && notRun.length > 0, authFailed: rows.filter(isAuthFail).length, notRun }
}
