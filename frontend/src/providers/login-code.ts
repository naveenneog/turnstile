/** The query parameter a CLI-assisted sign-in link carries its single-use code in. */
export const LOGIN_CODE_PARAM = "login_code"

/** A sign-in link's code, and the query string left once it is taken out.
 *
 *  The code is removed from the address as soon as it is read: it works once, so a reload
 *  or a copied link must not try it again, and it should not linger in the history. */
export function splitLoginCode(search: string): { code: string | null; rest: string } {
  const params = new URLSearchParams(search)
  const code = params.get(LOGIN_CODE_PARAM)?.trim() || null
  params.delete(LOGIN_CODE_PARAM)
  const rest = params.toString()
  return { code, rest: rest ? `?${rest}` : "" }
}