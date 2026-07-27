// Shared loading/error presentational states so a failed request reads as
// "something is actually wrong" instead of a page that quietly renders
// nothing (see hooks.js — that used to be every page's default behavior).

export function PageSkeleton() {
  return (
    <div className="animate-pulse flex flex-col gap-3" aria-busy="true" aria-live="polite">
      <div className="h-5 w-52 bg-white/5 rounded-lg mb-1" />
      <div className="h-3.5 w-full max-w-[520px] bg-white/[0.04] rounded-lg mb-5" />
      <div className="grid gap-3 grid-cols-1 sm:grid-cols-3">
        <div className="h-20 bg-white/[0.03] rounded-2xl" />
        <div className="h-20 bg-white/[0.03] rounded-2xl" />
        <div className="h-20 bg-white/[0.03] rounded-2xl" />
      </div>
      <div className="h-40 bg-white/[0.025] rounded-2xl mt-2" />
    </div>
  )
}

export function PageError({ message = "Couldn't load this page.", detail }) {
  return (
    <div className="mt-6 px-8 py-14 text-center bg-white/[0.025] border border-dashed rounded-[18px]"
         style={{ borderColor: 'rgba(244,63,94,0.3)' }}>
      <div className="w-[52px] h-[52px] mx-auto mb-[18px] rounded-[14px] flex items-center justify-center text-[22px]"
           style={{ background: 'rgba(244,63,94,0.1)', color: '#FB7185' }}>⚠</div>
      <div className="text-[15px] font-semibold mb-2" style={{ color: '#FB7185' }}>{message}</div>
      {detail && <div className="text-[11.5px] text-dim max-w-[460px] mx-auto font-mono leading-relaxed">{detail}</div>}
      <div className="text-[12px] text-dim mt-4">
        Check the data-health banner above — if an upstream API is failing, that's almost
        certainly why. Otherwise, try refreshing.
      </div>
    </div>
  )
}
