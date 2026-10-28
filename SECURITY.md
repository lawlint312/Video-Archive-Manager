# 安全政策 ／ Security Policy

[繁體中文](#繁體中文) ｜ [English](#english)

---

## 繁體中文

### 支援的版本

VAM 只有一位維護者，安全修正只會出現在**最新的 Release**。發現問題時，請先確認在最新版上仍然可以重現。

| 版本 | 是否修正 |
|------|---------|
| 最新的 Release | ✅ |
| 更早的版本 | ❌（請升級） |

### 如何回報

**請不要開公開的 Issue。** 架設 VAM 的人可能已經把它開放到外網，漏洞細節在修正之前公開，會讓他們暴露在風險中。

請改用 GitHub 的私下回報：到本 repo 的 **Security** 分頁，按 **Report a vulnerability**。只有維護者看得到回報內容。

回報時請盡量附上：

- 受影響的版本，以及作業系統
- 問題的類型與影響（例如：未登入就能讀到檔案、受限帳號能看到不屬於自己的資料夾）
- 重現步驟，最好附上實際送出的請求
- 需要的前提條件（是否要先登入、需要哪種權限、是否要開放對外連線）

### 回報之後

- 我會盡量在 **7 天內**回覆，確認收到並說明初步判斷。
- 確認是漏洞之後，會先修正並發布新版，再公開說明。你希望的話，會在版本說明中署名致謝。
- 這是個人在業餘時間維護的專案，沒有懸賞獎金。

### 範圍

**屬於安全問題的，例如：**

- 未登入就能存取 API、影片串流或檔案
- 繞過權限：受限帳號能存取允許範圍以外的資料夾，或執行沒有權限的操作
- 路徑穿越：讀取、寫入或刪除收藏根目錄以外的檔案
- 繞過兩步驟驗證、登入節流，或者 session 可以被盜用、偽造
- 跨站腳本（XSS）、跨站請求偽造（CSRF），包括透過檔名、匯入的網頁、NFO 內容觸發的
- 透過檔名或使用者輸入，在 FFmpeg 等外部程式上執行任意指令

**不屬於這裡的：**

- 部署方式造成的問題：例如沒有 HTTPS 就直接開放到外網、管理員密碼太弱。建議的對外架設方式請見 README 的「帳號、權限與安全」。
- 需要管理員權限才能做的事：管理員本來就能存取所有資料夾與設定。
- 第三方套件本身的漏洞：請回報給該套件。如果 VAM 用到了有已知漏洞的版本，歡迎透過上面的方式告訴我。
- VAM 查詢資料用的外部網站本身的問題。

---

## English

### Supported versions

VAM has a single maintainer, and security fixes go into the **latest Release** only. Please check that the problem still reproduces there.

| Version | Fixed |
|---------|-------|
| Latest Release | ✅ |
| Older versions | ❌ (please upgrade) |

### Reporting a vulnerability

**Please do not open a public issue.** People who run VAM may have exposed it to the internet, and publishing details before a fix puts them at risk.

Use GitHub's private reporting instead: open the **Security** tab of this repository and choose **Report a vulnerability**. Only the maintainer can see the report.

Please include, where you can:

- the affected version and your operating system
- the kind of issue and its impact (for example: reading files without signing in, a restricted account seeing folders outside its scope)
- steps to reproduce, ideally the actual requests sent
- the preconditions (signed in or not, which permissions, whether the server is reachable from outside)

### What happens next

- I aim to reply **within 7 days** to confirm receipt and give a first assessment.
- Once confirmed, a fix is released first and the issue is disclosed afterwards. You will be credited in the release notes if you wish.
- This is a personal project maintained in spare time; there is no bug bounty.

### Scope

**In scope, for example:**

- reaching the API, video streams or files without signing in
- permission bypass: a restricted account reaching folders outside its allowed roots, or performing actions it has no permission for
- path traversal: reading, writing or deleting files outside the library roots
- bypassing two-factor authentication or login throttling; stealing or forging sessions
- cross-site scripting (XSS) or cross-site request forgery (CSRF), including through file names, imported web pages or NFO content
- running arbitrary commands through FFmpeg or other external programs via file names or user input

**Out of scope:**

- deployment choices, such as exposing VAM to the internet without HTTPS or using a weak admin password. See "Accounts, permissions and security" in the README for the recommended setup.
- anything that requires the admin account, which by design can reach every folder and setting
- vulnerabilities in third-party packages themselves: please report those upstream. If VAM ships a version with a known vulnerability, do tell me through the channel above.
- problems with the external websites VAM looks data up from
