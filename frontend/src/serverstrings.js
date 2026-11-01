// Text the server sends, translated on arrival.
//
//   ts('唯讀')                           → Read-only
//   ts('連續 5 次失敗，已開始鎖定')        → 5 failures in a row, locked
//   ts('鎖定中（帳號+位址，還有 42 秒）')  → the inner 帳號+位址 translated too
//
// The server keeps speaking Traditional Chinese — the API does not change,
// and rows already stored (the sign-in log, security events) translate the
// same way as new ones. A fixed string is looked up as it is; one with values
// in it is matched against TEMPLATES, and the values put back. A {why} or
// {list} value — words the server chose, not a file name — is translated as
// well (a 、 list item by item). Anything
// not listed is shown as the server wrote it, which is the same fallback t()
// has.
//
// backend/tests/test_serverstrings.py checks every entry here still appears in
// the backend source, so rewording one there fails a test instead of quietly
// showing Chinese again.
//
//   node tests/serverstrings.test.mjs
import { t, has, N_ } from './i18n.js'

// whole strings
export const FIXED = [
  // roles.py: the built-in roles, and the permission catalogue
  N_('管理員'), N_('可編輯'), N_('唯讀'),
  N_('整理'), N_('清理'), N_('影片處理'), N_('查詢'),
  N_('歸檔'), N_('待分類 → 角色資料夾'),
  N_('角色改名、整合資料夾'), N_('改名、合併同一人的資料夾'),
  N_('分類管理'), N_('側邊欄分類'),
  N_('編輯資料'), N_('個人資料、大頭照、備忘、片名'),
  N_('刪除檔案'), N_('刪除模式、待決配對'),
  N_('去重'), N_('重複掃描（影片、圖片）'),
  N_('人物辨識'), N_('人物辨識、人臉索引、相似排序'),
  N_('轉檔、修復'), N_('轉檔分頁'),
  N_('壓縮'), N_('壓縮分頁'),
  N_('剪輯'), N_('剪輯模式'),
  N_('角色查詢'), N_('個資、片名、WIKI／IAFD 匯入、NFO 讀寫'),
  // core/alerts.py: event names, and the fixed details
  N_('從沒用過的位址登入成功'), N_('有人嘗試不存在的帳號'), N_('連續失敗，已鎖定'),
  N_('同一位址猜遍多個帳號'), N_('兩步驟驗證變更'), N_('管理員刪除了別人的最愛'),
  N_('這個帳號第一次從這個位址登入成功'), N_('這個帳號不存在 —— 有人在猜帳號名稱'),
  N_('已啟用兩步驟驗證'), N_('自行關閉了兩步驟驗證'),
  N_('未知裝置'),
  // main.py / security.py: why a sign-in was refused
  N_('帳號或密碼錯誤'), N_('帳號已停用'), N_('來源 IP 不在允許清單內'),
  N_('兩步驟驗證碼錯誤'), N_('修改密碼：目前密碼錯誤'), N_('備用碼'),
  N_('帳號+位址'), N_('同一帳號被大量嘗試'),
  // main.py: job names
  N_('JAV個資查詢'), N_('JAV片名查詢'), N_('NFO 比對'), N_('建立演員對應表'),
  N_('壓縮掃描'), N_('重複掃描'), N_('圖片重複掃描'), N_('人臉索引'), N_('轉檔掃描'),
  N_('含 MP4'), N_('檢查影格時序'), N_('完整解碼檢查'), N_('檢查錄影中斷'),
  N_('全部重新檢查'),

  // ── error messages (HTTPException details and raised errors) ──
  // auth.py, security.py
  N_('請先設定兩步驟驗證'), N_('沒有編輯權限'), N_('這個帳號不允許從目前的網路位址存取'),
  N_('這是最常被猜的密碼之一，請換一個'), N_('密碼裡不能包含帳號名稱'),
  N_('密碼用的字元太少，請換一個'),
  // core/
  N_('檔案裡沒有頁面資料（__NEXT_DATA__），可能不是完整存檔的 AVBase 頁面'),
  N_('沒有任何資料庫可以備份'), N_('DMM 回的不是 JSON'),
  N_('不是分段 MP4（找不到 moof），無法用這個方式修復'),
  N_('丟掉所有片段後仍然讀不到，這個檔案修不了'), N_('修復後的檔案讀不到時長'),
  N_('找不到還能丟的片段，這個檔案修不了'), N_('找不到 MP4Box（需要安裝 GPAC）'),
  N_('無法讀取影片資訊'), N_('請用 年-月-日，例如 2026-10-22'),
  // main.py: signing in and accounts
  N_('驗證階段已逾時，請重新登入'), N_('密碼不正確'), N_('請先開始設定'),
  N_('驗證碼不正確，請確認手機時間是否準確'),
  N_('這個帳號有編輯權限，必須保持兩步驟驗證；請先請管理員調整權限組'),
  N_('這個帳號沒有啟用兩步驟驗證'), N_('驗證碼不正確'), N_('新密碼不能和目前的密碼相同'),
  N_('驗證碼錯誤次數過多，請重新登入'), N_('這個帳號已停用'),
  N_('這個帳號不允許從目前的網路位址登入'), N_('目前的密碼不正確'),
  N_('找不到這個登入'), N_('不能刪除自己的帳號'), N_('必須至少保留一個管理員'),
  N_('不能停用自己的帳號'), N_('必須至少保留一個可用的管理員'),
  N_('指定的資料夾都不在設定的根目錄清單中，無法套用限制'),
  // roles.py
  N_('沒有這個權限組'), N_('請輸入權限組名稱'), N_('名稱最多 30 個字'),
  N_('管理員的權限固定，不能修改'), N_('找不到這個權限組'), N_('管理員不能刪除'),
  N_('內建的權限組不能刪除，可以改名或調整勾選'), N_('已經有同名的權限組'),
  // main.py: everything else
  N_('請先指定備份資料夾'), N_('請至少選擇一位角色'), N_('找不到選取的角色'),
  N_('選取的角色底下沒有可辨識的番號'), N_('缺少番號或檔案路徑'),
  N_('mode 必須是 import 或 export'), N_('沒有可以復原的寫入'), N_('圖庫裡沒有這張'),
  N_('請至少選擇一個資料夾'), N_('這份紀錄已不存在'), N_('無效的 crf'),
  N_('無效的解析度上限'), N_('沒有選取任何檔案'), N_('資料夾不存在或無權存取'),
  N_('這個資料夾裡沒有照片'), N_('這個工作無法中途停止'), N_('已沒有這項功能的權限'),
  N_('找不到這個中斷的工作'), N_('尚未設定歸檔根目錄（⚙ 設定）'), N_('來源與目標相同'),
  N_('無法產生縮圖表'), N_('沒有選取任何片段'), N_('縮圖表已取消'), N_('片段格式錯誤'),
  N_('沒有這個資料夾的權限'), N_('寫入之後被改過，保留現在的內容'), N_('影片已經不在了'),
  N_('預覽之後有人建立了這個檔，沒有覆蓋'),
  // messages built in a helper, not raised directly
  N_('請使用完整路徑，例如 D:\\VAM備份'), N_('只接受內網或 VPN 連線'),
  N_('已經全部完成，不需要繼續'),
  N_('這是第一次啟動時自動產生的密碼，已經寫在伺服器的日誌檔裡，請換一個新密碼'),
  N_('已標為我的最愛 —— 請先在播放器取消最愛再刪除'),
  N_('這個檔案正在產生縮圖表或轉檔，背景工作結束後就能刪除（通常幾秒）'),
  N_('這個檔案裡找不到作品或藝名，可能不是演員頁'),
  N_('上一次的暫存檔還在，請先手動刪除'),
  N_('找不到可修復的斷訊（檔案可能已經變了）'), N_('找不到可去除的空白（檔案可能已經變了）'),

  // ── convert: why each row is what it is (joined with ；, split on display) ──
  N_('片段損壞，可嘗試修復'), N_('無法讀取（檔案損毀或不是影片）'),
  N_('換殼（無損，不重新編碼）'), N_('修正影格時序（無損，驗證後取代原檔）'),
  N_('已經是可播放的 MP4'), N_('同名的 .mp4 已存在'), N_('驗證後會取代原檔'),
  N_('需要安裝 GPAC 才能修復'),
  // retime.py: why a file stutters, and why it cannot be fixed
  N_('影格時序錯誤（有 B 影格卻沒有顯示時間表，瀏覽器會掉格）'),
  N_('只支援 H.264（MP4Box 的 POC 解析是逐編碼器實作的）'),
  N_('有多條音軌，目前的流程只會保留第一條'),
  // verify(): why a result was thrown away
  N_('輸出檔不存在或為空'), N_('輸出檔無法讀取'), N_('輸出檔沒有視訊軌'),
  N_('無法完整解碼驗證輸出檔'), N_('輸出檔讀不到影像封包'),
  N_('時序沒有被修正（輸出仍然沒有顯示時間表）'),
  N_('壓縮後反而更大，已捨棄（這個檔案本來就編得很有效率）'),

  // ── progress: phases, stages, steps ──
  N_('走訪檔案'), N_('比對相同大小'), N_('讀取時長'), N_('幀比對'),
  N_('走訪圖片'), N_('相似比對'), N_('比對浮水印'), N_('讀取照片'), N_('辨識人物'),
  N_('定位損壞處'), N_('完整解碼驗證'), N_('檢查聲音'), N_('分析斷訊'), N_('分析空白'),
  N_('完整解碼驗證（需要幾分鐘）'),
  N_('抽出影像位元流'), N_('抽出音訊'), N_('重建影格時序'),
  N_('複製到本機'), N_('找出壞掉的片段'), N_('重新封裝'),
  N_('檢查格式'), N_('檢查錄影斷訊'), N_('整理影片清單…'),

  // ── what is unavailable, and why ──
  N_('未偵測到 CUDA GPU，人臉辨識已停用'), N_('CUDA 無法載入，人臉功能正以 CPU 執行'),
  N_('CUDA 不可用，將以 CPU 執行（約慢 4.75 倍，且會佔滿 CPU）'),
  // idolprofile.py: the vote
  N_('沒有查到任何演員'),
  // dedup.py: what a folder merge left behind
  N_('來源資料夾在 NAS 上仍殘留一個項目，可能需要到 DSM File Station 手動刪除'),
  // the convert history's option tags
  N_('影格時序'), N_('完整解碼'), N_('錄影中斷'),
  // the result of resolving a pending pair
  N_('另一個檔案已經不在了'),
]

// strings with values in them; {name} matches anything
export const TEMPLATES = [
  N_('連續 {n} 次失敗，已開始鎖定'),
  N_('這個位址 15 分鐘內失敗 {n} 次'),
  N_('刪除了 {n} 位使用者的最愛：{name}'),
  N_('兩步驟驗證被 {name} 重設'),
  N_('鎖定中（{why}，還有 {n} 秒）'),
  N_('鎖定中（修改密碼，還有 {n} 秒）'),
  N_('寫入 NFO（{n} 位角色）'),
  N_('匯入 NFO（{n} 位角色）'),
  N_('壓縮 {n} 個檔案'),
  N_('轉檔 {n} 個檔案'),
  N_('人物辨識：{name}'),
  N_('相似掃描：{name}'),
  N_('整包歸檔 {name} → {person}'),
  N_('資料夾整合 {name} → {dest}'),
  N_('剪輯 {name}（{n} 段）'),
  N_('剪輯 {name}'),
  N_('歸檔 {name}'),
  N_('轉檔掃描（{list}）'),
  N_('重啟後已自動接續 {n} 次又被中斷，這次等你決定'),
  N_('無法接續：{error}'),

  // ── error messages ──
  N_('請先修改密碼：{reason}'),
  N_('沒有「{perm}」的權限'),
  N_('密碼至少 {n} 個字元'),
  N_('密碼還缺少：{list}'),
  N_('不是作品頁或演員頁（{value}）'),
  N_('找不到 {name}，備份中止'),
  N_('DMM 拒絕了這個查詢：{error}'),
  N_('DMM 回應 HTTP {status}'),
  N_('無法連線到 DMM：{error}'),
  N_('無法連線到 javdatabase：{error}'),
  N_('不是電影的 NFO（根元素是 <{tag}>）'),
  N_('不是電影的 NFO（根元素是 <{tag}>），不寫入'),
  N_('不是有效的 XML：{error}'),
  N_('「{text}」缺少四位數的年份。{hint}'),
  N_('看不出「{text}」是哪一天。{hint}'),
  N_('「{text}」不是日期。{hint}'),
  N_('「{text}」分不出哪個是月、哪個是日。{hint}'),
  N_('年份 {y} 不合理。{hint}'),
  N_('沒有 {m} 月。{hint}'),
  N_('{y} 年 {m} 月沒有 {d} 日。{hint}'),
  N_('圖庫裡沒有這張：{cat}/{file}'),
  N_('「{name}」已存在'),
  N_('空間不足：預估需要 {need} GB，剩餘 {free} GB'),
  N_('嘗試次數過多，請在 {n} 秒後再試'),
  N_('備份失敗：{error}'),
  N_('這份清單不包含你目前的位址（{ip}），儲存後你會立刻被鎖在外面'),
  N_('圖庫下載失敗：{error}'),
  N_('根目錄不在允許清單中：{path}'),
  N_('{error}（WinError 32，重試 {n} 次後仍失敗）'),
  N_('還有 {n} 個帳號屬於這個權限組，請先把他們改到別的組'),
  N_('無法寫入這個資料夾：{error}'),
  N_('看不懂的 IP 或網段：{value}'),
  N_('有 {n} 位使用者標為我的最愛 —— 需要確認後才能刪除'),
  N_('有 {n} 位使用者標為我的最愛 —— 只有管理員可以確認後刪除'),
  N_('檔案過大（超過 {n} MB）'),
  N_('解析失敗：{error}'),
  N_('驗證失敗，已保留原檔：{problem}'),
  N_('已保留原檔：{problem}'),
  N_('轉檔成功但原檔刪除失敗：{error}'),
  N_('轉檔成功但無法取代原檔：{error}'),
  N_('已刪除原檔，但改名失敗：{error}'),
  N_('查詢失敗：{error}'),
  N_('片段太短（至少 {n} 秒）'),

  // ── convert reasons, each part on its own ──
  N_('換殼＋音訊轉 AAC（視訊無損，來源音訊 {codec}）'),
  N_('需重編碼（{codec} 在瀏覽器支援不穩定）'),
  N_('需重編碼（像素格式 {pix}，瀏覽器不支援）'),
  N_('需重編碼（視訊為 {codec}）'),
  N_('聲音有 {n} 個壞封包，換殼時會一併重新編碼聲音'),
  N_('聲音另有 {n} 個壞封包'),
  N_('有 {n} 段空白無法無損去除（空白中仍有聲音，或空白後不是關鍵影格）'),
  N_('另有錄影中斷 {n} 處（共 {len}），先處理這個，之後再掃一次'),
  N_('音訊為 {codec}，目前只驗證過 AAC'),
  N_('畫面損壞（{level}，解碼錯誤 {n} 行）'),
  // verify()
  N_('時長不符（預期 {want}s，實際 {got}s）'),
  N_('時長不符（原檔 {want}s，輸出 {got}s）'),
  N_('損壞太嚴重：修完只剩 {got} 秒（原檔 {want} 秒）'),
  N_('視訊編碼改變了（{from} → {to}）'),
  N_('聲音仍有 {n} 個壞掉的封包'),
  N_('修復後仍有畫面錯誤 {n} 行、聲音錯誤 {m} 行'),
  N_('重新編碼後仍有畫面錯誤 {n} 行、聲音錯誤 {m} 行'),
  N_('畫格數不符（原檔 {want}，輸出 {got}）'),
  N_('影格數不符（原檔 {want}，輸出 {got}）'),
  N_('輸出仍有 {n} 段空白'),
  N_('輸出仍然不是瀏覽器能播的編碼（{codec}）'),
  N_('輸出的像素格式仍不支援（{pix}）'),

  // ── progress ──
  N_('{check}：{name}（{k}/{total}，另 {n} 支沿用上次結果）'),
  N_('{check}：{name}（{k}/{total}）'),
  N_('掃描：{name}'),
  N_('上次共 {n} 個'),
  N_('上次共 {n} 張'),
  N_('找出壞掉的片段（第 {n} 輪）'),
  N_('檢查：{name}'),
  N_('讀取 NFO：{k}/{total}'),
  N_('比對 NFO：{k}/{total}'),
  N_('寫入 NFO：{k}/{total} {name}'),

  // ── unavailable, the vote, merges, busy files ──
  N_('缺少套件：{name}'),
  N_('找不到模型：{path}'),
  N_('多數是「{name}」（{n}/{total}），名稱與資料夾不同 —— 可能是改藝名，請確認'),
  N_('票數不足或分散（{n}/{total}）'),
  N_('來源資料夾無法刪除：{error}'),
  N_('來源資料夾仍有 {n} 個檔案未處理'),
]

const FIXED_SET = new Set(FIXED)
// params whose value is the server's own words, so they are translated too:
// a lock reason, a 、 list of options, a password problem, a permission name,
// the date example tacked onto every date error
const SERVER_WORDS = new Set(['why', 'list', 'reason', 'perm', 'hint',
                              'problem', 'check', 'level'])
const esc = s => s.replace(/[.*+?^$()|[\]\\]/g, '\\$&')
// most specific first, so 剪輯 {name}（{n} 段） wins over 剪輯 {name}
const COMPILED = TEMPLATES
  .map(k => ({
    k,
    names: [...k.matchAll(/\{(\w+)\}/g)].map(m => m[1]),
    re: new RegExp('^' + esc(k).replace(/\\?\{\w+\\?\}/g, '(.+?)') + '$', 's'),
    weight: k.replace(/\{\w+\}/g, '').length,
  }))
  .sort((a, b) => b.weight - a.weight)

function value(v) {
  if (has(v)) return t(v)
  // a list the server joined with 、 (the convert scan's options)
  if (v.includes('、')) {
    const parts = v.split('、')
    if (parts.every(p => has(p))) return parts.map(p => t(p)).join(t('、'))
  }
  // a whole sentence of its own (the password problem inside 請先修改密碼：…)
  return ts(v)
}

/** The server's text in the interface language, or as it came. */
export function ts(text) {
  if (text == null) return ''
  const s = String(text)
  if (!s) return s
  if (FIXED_SET.has(s) || has(s)) return t(s)
  for (const c of COMPILED) {
    const m = c.re.exec(s)
    if (!m) continue
    const p = {}
    // only {why} and {list} carry server words; {name} and the rest are the
    // user's own (file and folder names) and stay as they are
    c.names.forEach((n, i) => { p[n] = SERVER_WORDS.has(n) ? value(m[i + 1]) : m[i + 1] })
    return t(c.k, p)
  }
  // A convert row's reason is several findings joined with ；
  // ("需重編碼（視訊為 mpeg4）；驗證後會取代原檔"): each one on its own.
  if (s.includes('；')) {
    const parts = s.split('；')
    const out = parts.map(ts)
    if (out.some((x, i) => x !== parts[i])) return out.join(t('；'))
  }
  return s
}
