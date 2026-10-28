<script setup>
/**
 * One place that maps semantic icon names to Lucide components.
 * Icons are monochrome and inherit the surrounding text colour, so they follow
 * the theme instead of the OS emoji font.
 *
 *   <Icon name="home" />            default 16px, current colour
 *   <Icon name="star" :size="14" /> custom size
 */
import {
  House, Inbox, Film, IdCard, Search, Settings, ScanFace, Package, Star,
  Image, Folder, FolderOpen, FolderSync, Pencil, Trash2, Play, User,
  TriangleAlert, RefreshCw, Copy, Tags, Check, X, SkipForward, Eye,
  Wrench, ChevronRight, ChevronDown, ChevronLeft, Plus, ArrowUp, ArrowDown,
  StickyNote, Keyboard, LoaderCircle, ExternalLink, SquareCheck, Square,
  CircleDot, Camera, LayoutGrid, Rows3, LogOut, Gauge, Scissors,
  FlagTriangleRight, FlagTriangleLeft, Heart, Info, History,
} from 'lucide-vue-next'

const MAP = {
  home: House, inbox: Inbox, film: Film, profile: IdCard, search: Search,
  settings: Settings, face: ScanFace, package: Package, star: Star,
  image: Image, folder: Folder, folderOpen: FolderOpen, merge: FolderSync,
  pencil: Pencil, trash: Trash2, play: Play, user: User, warn: TriangleAlert,
  refresh: RefreshCw, copy: Copy, tags: Tags, check: Check, close: X,
  skip: SkipForward, eye: Eye, wrench: Wrench, chevronRight: ChevronRight,
  chevronDown: ChevronDown, chevronLeft: ChevronLeft, plus: Plus,
  up: ArrowUp, down: ArrowDown, note: StickyNote, keyboard: Keyboard,
  spinner: LoaderCircle, link: ExternalLink, checked: SquareCheck,
  unchecked: Square, dot: CircleDot, camera: Camera, grid: LayoutGrid,
  rows: Rows3, logout: LogOut, gauge: Gauge, scissors: Scissors,
  markIn: FlagTriangleRight, markOut: FlagTriangleLeft, heart: Heart,
  info: Info, history: History,
}

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 16 },
  stroke: { type: [Number, String], default: 2 },
})
</script>

<template>
  <component
    :is="MAP[name] || MAP.dot"
    :size="Number(size)"
    :stroke-width="Number(stroke)"
    class="vam-icon"
  />
</template>

<style scoped>
.vam-icon { vertical-align: -0.18em; flex-shrink: 0; }
</style>
