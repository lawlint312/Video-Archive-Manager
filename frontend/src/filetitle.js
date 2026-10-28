// Which rows in the grid a title edit should update.
//
// The server decides the identity and says so in the response; this only
// applies it. Two cases, and getting them the wrong way round is destructive:
//
//   video_id present → the title belongs to the film, so every copy updates
//   video_id empty   → the title belongs to one file, so match on the path
//
// The empty case is why this is a function and not an inline `||`. Every
// video without a catalogue number carries `video_id: ''` — 95% of this
// library — so a plain `v.video_id === d.video_id` would match all of them at
// once and stamp one title across the whole page.

export function titleTargets(videos, res) {
  const byId = !!res.video_id
  return (videos || []).filter(v =>
    byId ? v.video_id === res.video_id : v.path === res.path)
}
