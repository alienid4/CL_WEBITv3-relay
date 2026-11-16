// 機櫃類寫入（拖拉搬移、調高度、合併／拆開、已不在櫃上、位置更正、4-5 確認／判讀錯了）共用：
// 伺服器回 428（登入停用＋設定「寫入要管理密碼」打開）時，跳出輸入框問管理密碼，帶 X-Admin-Password 重送一次。
// 密碼只放在這一次請求的標頭，不存瀏覽器。429＝錯太多次，直接顯示伺服器的訊息。
export function useGuardedFetch() {
  const { apiFetch } = useApi()
  async function guardedFetch<T = any>(path: string, opts: any = {}): Promise<T> {
    try {
      return await apiFetch<T>(path, opts)
    } catch (e: any) {
      const code = e?.statusCode ?? e?.response?.status
      if (code !== 428) throw e
      const pw = window.prompt(`${e?.data?.detail || '這個動作要輸入管理密碼'}\n（admin 帳號的密碼；只用這一次，不會存起來）`)
      if (!pw) throw e
      return await apiFetch<T>(path, { ...opts, headers: { ...(opts.headers || {}), 'X-Admin-Password': pw } })
    }
  }
  return { guardedFetch }
}
