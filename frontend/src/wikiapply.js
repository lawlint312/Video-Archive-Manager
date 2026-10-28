/**
 * The body of a wiki import, built where it can be tested.
 *
 * This is a pure function for one reason: a field the server reads and the
 * client forgets to send fails silently and looks like something else
 * entirely. It happened twice in one change —
 *
 *   `works` was added to the server model and to the preview response, but
 *   never to the request, so the "not yet acquired" list stayed empty and
 *   reported the import as an old one.
 *
 *   `folder` was only sent when the alias checkbox was ticked, and the server
 *   needs it to store the summary at all — so unticking aliases quietly threw
 *   the statistics away too.
 *
 * Neither is visible by reading the component. Both are obvious against a list
 * of what the server actually consumes.
 *
 *   node tests/wikiapply.test.mjs
 */

// The two sources send *different* shapes on purpose: an IAFD import has no
// titles, aliases or cast, and a wiki import has no bio. So the guarantee this
// file exists for ("did we remember to send everything the server reads") is
// kept per source rather than by one union that neither branch satisfies.
export const WIKI_FIELDS = ['titles', 'aliases', 'folder', 'cast', 'source',
                            'released', 'summary', 'all_ids', 'works',
                            'page_name', 'catalog']
export const IAFD_FIELDS = ['site', 'folder', 'profile', 'file_released']
export const AVBASE_FIELDS = ['site', 'folder', 'titles', 'released', 'cast',
                              'aliases', 'profile', 'relations', 'source',
                              'works', 'page_name', 'catalog']

/** Every field `/api/wiki/apply` reads. Keep in step with `WikiApplyBody`. */
export const FIELDS = [...new Set([...WIKI_FIELDS, ...IAFD_FIELDS, ...AVBASE_FIELDS])]

// What a stored relation needs: the two numbers, and whatever the page knew
// of their dates and titles (the verdict is worked out again on the server).
const REL_KEYS = ['parent', 'child', 'parent_date', 'child_date',
                  'parent_title', 'child_title']

export function buildApplyBody(page, choice) {
  const c = choice || {}
  if (page.site === 'avbase.net') {
    // Titles and dates go into the shared catalogue, read below anything VAM
    // already holds, so these are offers, not overwrites. `titles` and
    // `released` are only the counts' source: the films here they show on.
    return {
      site: 'avbase.net',
      folder: c.folder || '',
      // Always sent: with a character chosen it is also the list the ⓘ
      // panel counts. `catalog` says whether its titles are kept.
      works: Array.isArray(page.works) ? page.works : [],
      catalog: !!c.titles,
      page_name: page.name || '',
      titles: c.titles ? (page.titles || {}) : {},
      released: c.titles ? (page.released || {}) : {},
      cast: c.cast ? (page.cast || {}) : {},
      aliases: c.aliases ? (page.aliases || []) : [],
      profile: c.profile ? (page.profile || {}) : {},
      relations: c.relations
        ? (page.relations || []).map(r => Object.fromEntries(REL_KEYS.map(k => [k, r[k] || ''])))
        : [],
      source: page.url || '',
    }
  }
  if (page.site === 'iafd.com') {
    // Deliberately narrow: an IAFD import writes a bio and some release years.
    // It never writes a title, an alias or a cast list, so those fields are
    // not merely empty here — they are absent by design.
    return {
      site: 'iafd.com',
      folder: c.folder || '',
      profile: c.profile ? (page.profile || {}) : {},
      // {path: year} for the confirmed matches only.
      file_released: c.released
        ? Object.fromEntries((page.matched || []).map(m => [m.path, m.year]))
        : {},
    }
  }
  return {
    titles: c.titles ? (page.titles || {}) : {},
    aliases: c.aliases ? (page.aliases || []) : [],
    // Sent whichever boxes are ticked: the server needs it to file the
    // summary against a character, and it decides separately (on the alias
    // list being non-empty) whether to touch person_info.json.
    folder: c.folder || '',
    cast: c.cast ? (page.cast || {}) : {},
    // Rides with the titles: same rows, same page, and a date is not
    // something anyone would want withheld separately.
    released: c.titles ? (page.released || {}) : {},
    source: page.url || '',
    // Always sent. Reading the page may simply have been to find out how much
    // of this performer's output is already here, with nothing imported.
    summary: page.summary || null,
    all_ids: page.all_ids || [],
    works: page.works || [],
    // every work's title and date into the shared catalogue — with the
    // titles box, like the library's own titles above
    catalog: !!c.titles,
    page_name: page.name || '',
  }
}
