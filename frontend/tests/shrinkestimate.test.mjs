// The shrink panel's estimate: follows the settings, learns from real results.
//   node tests/shrinkestimate.test.mjs
import { baseSaving, crfFactor, pixelsKept, predictedOut, calibrate,
         estimateSaving, H265_FACTOR } from '../src/shrinkestimate.js'

let failed = 0
const check = (ok, msg) => { console.log((ok ? '  ok   ' : '  FAIL ') + msg); if (!ok) failed++ }
const near = (a, b, tol = 0.01) => Math.abs(a - b) <= tol

const GB = 1024 ** 3
// the film that started this: 32.17 Mbps 4K, 8.58 GB, H.265 crf 20 → 3.23 GB saved
const film4k = { mbps: 32.17, width: 3840, height: 2160, size: 8.58 * GB }
const film4kRun = { ...film4k, crf: 20, codec: 'h265', max_edge: 0,
                  before: film4k.size, after: film4k.size - 3.23 * GB }
const DEFAULT = { crf: 26, codec: 'h264', maxEdge: 0 }

console.log('\n基準：crf 26、H.264、不縮，和後端的曲線一樣')
check(near(baseSaving(6.1), 0.62) && near(baseSaving(52), 0.86) &&
      near(baseSaving(100), 0.86) && near(baseSaving(1), 0.23), '曲線端點')
check(near(1 - predictedOut(film4k, DEFAULT), baseSaving(32.17)), '預設設定 = 原本的粗估')
check(near(estimateSaving(film4k, DEFAULT, null) / GB, 7.12, 0.02),
      `那支 4K 原本估 ${(estimateSaving(film4k, DEFAULT, null) / GB).toFixed(2)} GB（畫面上是 7.12）`)

console.log('\ncrf：數字越小留越多，對照表的兩個來源都對得上')
check(near(crfFactor(20, 6.1), 1.97) && near(crfFactor(20, 52), 2.57), 'crf 20 兩端')
check(near(crfFactor(26, 30), 1), 'crf 26 不變')
check(crfFactor(20, 6.1) < crfFactor(20, 20) && crfFactor(20, 20) < crfFactor(20, 52),
      '中間依位元率內插')
check(near(crfFactor(20, 1), 1.97) && near(crfFactor(20, 200), 2.57), '超出範圍不外插')
// the factor is relative: the table's own 6.1 Mbps file saved 70% → 41%, and
// the curve's 62% at that bitrate becomes 25% the same way
check(near(1 - predictedOut({ mbps: 6.1, width: 1920, height: 1080 },
                            { crf: 20, codec: 'h264', maxEdge: 0 }), 0.25, 0.02),
      '一般 1080p crf 20 → 約省 25%（基準曲線 62% 的輸出 ×1.97）')
const order = [20, 23, 26, 28].map(c => estimateSaving(film4k, { ...DEFAULT, crf: c }, null))
check(order.every((v, k) => !k || v > order[k - 1]), 'crf 越大估得越多')

console.log('\nH.265 與縮小解析度')
check(near(predictedOut(film4k, { ...DEFAULT, codec: 'h265' }) /
           predictedOut(film4k, DEFAULT), H265_FACTOR), 'H.265 輸出 ×0.69')
check(pixelsKept(3840, 2160, 1920) === 0.25 && pixelsKept(1920, 1080, 1920) === 1 &&
      pixelsKept(1080, 1920, 1280) === (1280 / 1920) ** 2 && pixelsKept(3840, 2160, 0) === 1,
      '像素比例：直式看長邊、不放大、0 = 不縮')
// edge table: 4K crf 26 kept 14% full size, ~4% scaled to 1080
const fourK = { mbps: 52, width: 3840, height: 2160 }
check(near(predictedOut(fourK, { ...DEFAULT, maxEdge: 1920 }), 0.04, 0.005),
      '4K 縮 1080 crf 26 → 約留 4%（實測表）')

console.log('\n校正：沒有結果就不動，有結果就往實際靠')
const none = calibrate([])
check(none.all === 1 && none.h264 === 1 && none.h265 === 1 && none.n.all === 0, '沒有結果 = 1')
const one = calibrate([film4kRun])
const before = estimateSaving(film4k, { crf: 20, codec: 'h265', maxEdge: 0 }, none)
const after1 = estimateSaving(film4k, { crf: 20, codec: 'h265', maxEdge: 0 }, one)
check(after1 < before && after1 > 3.23 * GB,
      `一支就往實際移，但不一次跳過去：${(before / GB).toFixed(2)} → ${(after1 / GB).toFixed(2)} GB（實際 3.23）`)
const many = calibrate(Array(20).fill(film4kRun))
const after20 = estimateSaving(film4k, { crf: 20, codec: 'h265', maxEdge: 0 }, many)
check(near(after20 / GB, 3.23, 0.35), `同樣的結果累積 20 支 → ${(after20 / GB).toFixed(2)} GB`)
check(one.n.h265 === 1 && one.n.h264 === 0, '依編碼分開計數')
check(one.h264 > 1 && one.h264 < one.h265,
      'H.264 自己沒結果時借用整體的校正，但拉得比較少')
check(estimateSaving(film4k, { crf: 26, codec: 'h264', maxEdge: 0 }, one) <
      estimateSaving(film4k, DEFAULT, none), '其他設定也一起修正')
const exact = { ...film4k, crf: 26, codec: 'h264', max_edge: 0, before: film4k.size,
                after: film4k.size * predictedOut(film4k, DEFAULT) }
check(near(calibrate(Array(5).fill(exact)).h264, 1, 1e-9), '預測完全準 → 校正 = 1')
check(calibrate([{ before: 0, after: 5, mbps: 3 }, { before: 5, after: 0, mbps: 3 },
                 { before: 5, after: 3 }]).n.all === 0, '壞資料略過')

console.log('\n界線')
const tiny = { mbps: 52, width: 3840, height: 2160, size: 1000 }
check(estimateSaving(tiny, { crf: 28, codec: 'h265', maxEdge: 1280 }, none) <= 970,
      '最多估 97%')
const bad = calibrate(Array(30).fill({ ...film4kRun, after: film4kRun.before * 3 }))
check(estimateSaving(film4k, DEFAULT, bad) === 0, '校正後輸出比原檔大 → 估 0，不是負的')

console.log('\n' + (failed ? `${failed} 項失敗` : '全部通過'))
process.exit(failed ? 1 : 0)
