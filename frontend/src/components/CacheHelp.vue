<script setup>
/**
 * The explanation shared by the three cache-limit fields.
 *
 * A component rather than one block placed after the last of them: it was
 * written once and anchored at the bottom, so clicking the "?" on the first
 * field opened a panel two fields further down — you had to scroll to read it
 * and scroll back. Rendering the same component under whichever field was
 * asked keeps the answer next to the question.
 */
</script>

<template>
  <div class="helptbl">
    <div class="hh">這幾個上限各管什麼</div>
    <table>
      <thead>
        <tr><th></th><th>每個多大</th><th>整個庫大約</th><th>什麼時候用到</th></tr>
      </thead>
      <tbody>
        <tr><td>縮圖表</td><td>220 KB</td><td>看過的影片才有</td><td>點開縮圖表時</td></tr>
        <tr><td>影片縮圖</td><td>25 KB</td><td>400 MB</td><td>每次瀏覽</td></tr>
        <tr><td>照片縮圖</td><td>57 KB</td><td>2.9 GB（54,000 張）</td><td>瀏覽照片時</td></tr>
      </tbody>
    </table>
    <div class="hn">
      <b>影片縮圖這一項跟縮圖表無關</b> —— 管的是影片卡片上那張小圖。設得太小會讓它
      幾乎不被快取，畫面上看起來就是<b>「圖片很容易不顯示」</b>，而不是「有點慢」。
      <br>照片縮圖是個人資料頁的格子用的：格子只有 280px 寬，但照片常是手機原檔
      （3024×4032），不縮的話一個畫面要下載 43.9 MB、解碼 3.3 億像素。點開看大圖時
      仍然是原始檔案。
      <br><b>壓縮試看片段</b>固定 0.5 GB、沒有欄位可調 —— 那是暫存的 10 秒對照片段，
      看完就沒用了。它之所以佔空間：「原始」那半是<b>無損複製</b>，而會被拿去壓縮的
      正是高位元率的檔案，實測一次試看約 45 MB。
      <br>全部都是超過上限時<b>從最久沒看過的開始刪</b>。快取檔名是路徑的雜湊、無法
      反推來源，所以在檔案總管或 NAS 上直接刪掉影片時，它的快取不會自動消失 —— 上限
      就是那種情況的兜底。備忘（notes）不受任何上限影響，那是你自己寫的。
    </div>
  </div>
</template>

<style scoped>
/* Scoped styles do not cross components, so the look has to be repeated here
   rather than inherited from SettingsDialog. */
.helptbl { background: #1b1b2a; border: 1px solid #313148; border-radius: 6px;
  padding: 8px 10px; margin: 6px 0 2px; font-size: 11px; }
.hh { color: var(--accent); margin-bottom: 5px; }
table { border-collapse: collapse; width: 100%; margin-bottom: 6px; }
th, td { text-align: left; padding: 2px 10px 2px 0; font-weight: normal;
  color: var(--dim); }
th { color: #8b8ba7; }
.hn { color: var(--dim); line-height: 1.7; }
.hn b { color: var(--text); }
</style>
