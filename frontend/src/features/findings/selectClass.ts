// Shared Tailwind styling for the plain <select> controls in the findings feature.
// The two call sites differ only in sizing — the row status pickers are compact,
// the dialog field is full-width — so the common border/focus/disabled styling
// lives here once (DRY) and each variant prepends its own size classes.
const SELECT_BASE =
  'rounded-md border border-input bg-transparent shadow-xs outline-none ' +
  'transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] ' +
  'focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed ' +
  'disabled:opacity-50'

/** Compact inline picker (findings row status pickers). */
export const SELECT_CLASS_COMPACT = `h-8 px-2 py-1 text-xs ${SELECT_BASE}`

/** Full-width form field (the finding dialog's severity + node-link selects). */
export const SELECT_CLASS_FIELD = `h-9 w-full px-3 py-1 text-sm ${SELECT_BASE}`
