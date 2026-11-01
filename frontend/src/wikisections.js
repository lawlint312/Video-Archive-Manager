/**
 * Section names from the wiki, shown in Chinese.
 *
 * Display only. The Japanese name is what gets stored and what the grid filter
 * keys on (`wiki:sec:単体作品`), so translating server-side would strand every
 * page imported before the table changed. Here, improving a word costs nothing
 * and needs no re-import.
 *
 * A fixed table rather than a translation service, for the same reason the
 * performer-profile fields use one: this is a closed set of about a dozen
 * terms, and a lookup is exact, offline and free. Anything not in it is shown
 * as the wiki wrote it — a guess that reads plausibly is worse than a word the
 * user can look up.
 */
import { t, N_ } from './i18n.js'

// the values are keys too: Chinese is the source language, t() the rest
const NAMES = {
  '単体作品': N_('單體作品'),
  '総集編作品': N_('總集編'),
  'VR作品': N_('VR 作品'),
  '配信作品': N_('串流作品'),
  '同人サークル作品': N_('同人社團作品'),
  'イメージ作品': N_('寫真影片'),
  'その他作品(DVD・BD)': N_('其他作品（DVD／BD）'),
  'その他作品(配信系)': N_('其他作品（串流）'),
  // Seen on real pages but currently filtered out as non-video; translated
  // anyway so that if one ever does carry releases it does not appear raw.
  'その他作品': N_('其他作品'),
  'オリジナル作品': N_('原創作品'),
  '企画作品': N_('企劃作品'),
  'デジタル写真集': N_('數位寫真集'),
  // Not wiki sections: works only an AVBase page lists, split by how many
  // performers the card names (core/catalog.py).
  'AVBase:単体': N_('單人（僅 AVBase）'),
  'AVBase:共演': N_('多人／總集編（僅 AVBase）'),
}

export function sectionName(name) {
  return NAMES[name] ? t(NAMES[name]) : name || ''
}

export function hasTranslation(name) {
  return Object.prototype.hasOwnProperty.call(NAMES, name)
}

export const SECTION_NAMES = NAMES
