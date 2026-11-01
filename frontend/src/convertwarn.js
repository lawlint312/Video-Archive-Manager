// The confirmation text for a convert run.
//
// Extracted from `ConvertDialog.run()` because nothing could test it there:
// `mount.test.mjs` renders every component but never *calls* a click handler,
// so a `ReferenceError` inside one is invisible until someone presses the
// button. That happened — `rt` was referenced but never declared, and the
// only symptom was "開始轉檔 does nothing".
//
// It is also the text that warns about the destructive parts of this tool, so
// it is worth pinning down on its own merits: which actions delete the source,
// which replace it, and which are lossy.
import { t, N_ } from './i18n.js'

export function convertWarning(picked) {
  const items = picked || []
  const n = items.length
  const count = a => items.filter(i => i.action === a).length

  const re = count('reencode')
  const rp = count('repair')
  const rt = count('retime')
  const af = count('audiofix')
  const gf = count('gapfix')
  const gc = count('gapcut')
  // listed as "damaged", run as the concealing re-encode
  const cc = count('damaged') + count('conceal')
  // `in_place` is true for a retime too, but the sentence it drives says
  // "browsers cannot play these" and "lossy" — neither is true of a stream
  // copy of a file that already plays. It gets its own sentence below.
  // An audio fix is in place too, and equally misdescribed by that sentence:
  // the picture is copied untouched, only the audio is re-encoded.
  const ip = items.filter(i => i.in_place && i.action !== 'retime' &&
                               i.action !== 'audiofix' && i.action !== 'gapfix' &&
                               i.action !== 'gapcut').length

  let msg = t('處理 {n} 個檔案？\n\n轉檔的部分驗證通過後會刪除原始檔。', { n })
  const add = (k, c) => { if (c) msg += '\n\n' + t(k, { n: c }) }
  add(N_('⚠ 其中 {n} 個需要重編碼，畫質會有損失且無法還原。'), re)
  add(N_('⚠ 其中 {n} 個本來就是 .mp4，重編碼後會「直接取代原檔」（驗證通過才換）。這些是瀏覽器播不動的檔案。'), ip)
  add(N_('修復的 {n} 個「不會」刪除原始檔，而且無法事先知道會損失多少畫面。'), rp)
  add(N_('修正時序的 {n} 個會「直接取代原檔」（驗證通過才換）。畫面逐格原樣複製、不重編碼，畫質不變。'), rt)
  add(N_('修復聲音的 {n} 個會「直接取代原檔」（驗證通過才換）。影像原樣複製、畫質不變；聲音重新編碼（約 192 kbps），壞掉的片段補成靜音。'), af)
  add(N_('修復斷訊的 {n} 個只丟掉斷訊前殘缺的畫格、不重新編碼，修完會整支完整解碼驗證（每支需要幾分鐘），通過才取代原檔。'), gf)
  add(N_('遮蔽損壞的 {n} 個會整支重新編碼（crf 23）、另存成 xxx_fix.mp4，「不會」刪除原始檔。損壞的地方仍看得到花屏，但瀏覽器能正常播放與拖進度條；修完到「壓縮」分頁的待決定清單比對後再決定。'), cc)
  add(N_('去除空白的 {n} 個每一格都原樣複製、不重新編碼，只是片長變短；驗證畫格數相同、空白已消失才取代原檔。'), gc)
  return msg
}
