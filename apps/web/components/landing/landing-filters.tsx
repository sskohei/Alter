// SVG filters shared by the landing page. Rendered once, hidden from assistive tech.
// - lp-warp: slow liquid wobble for the logo (paused by MotionToggle / reduced motion)
// - lp-warp-still: same wobble without animation
// - lp-fx3d: RGB channel split + monochrome film grain for the 3D renders
export const WARP_SCALE = 6;

export function LandingFilters() {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true" focusable="false" data-smil="">
      <filter id="lp-warp" x="-10%" y="-40%" width="120%" height="180%">
        <feTurbulence type="fractalNoise" baseFrequency="0.006 0.035" numOctaves={2} seed={4} result="t">
          <animate attributeName="baseFrequency" dur="14s" values="0.006 0.035;0.011 0.06;0.006 0.035" repeatCount="indefinite" />
        </feTurbulence>
        <feDisplacementMap in="SourceGraphic" in2="t" scale={WARP_SCALE} xChannelSelector="R" yChannelSelector="G" />
      </filter>
      <filter id="lp-warp-still" x="-10%" y="-40%" width="120%" height="180%">
        <feTurbulence type="fractalNoise" baseFrequency="0.006 0.035" numOctaves={2} seed={4} result="t" />
        <feDisplacementMap in="SourceGraphic" in2="t" scale={WARP_SCALE} xChannelSelector="R" yChannelSelector="G" />
      </filter>
      <filter id="lp-fx3d" x="-10%" y="-10%" width="120%" height="120%" colorInterpolationFilters="sRGB">
        <feOffset in="SourceGraphic" dx={-4} result="ro" />
        <feColorMatrix in="ro" type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" result="r" />
        <feColorMatrix in="SourceGraphic" type="matrix" values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0" result="g" />
        <feOffset in="SourceGraphic" dx={4} result="bo" />
        <feColorMatrix in="bo" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" result="b" />
        <feBlend in="r" in2="g" mode="screen" result="rg" />
        <feBlend in="rg" in2="b" mode="screen" result="rgb" />
        <feTurbulence type="fractalNoise" baseFrequency="1.5" numOctaves={2} stitchTiles="stitch" result="n" />
        <feColorMatrix in="n" type="saturate" values="0" result="nc" />
        <feComponentTransfer in="nc" result="na">
          <feFuncR type="linear" slope={2.4} intercept={-0.7} />
          <feFuncG type="linear" slope={2.4} intercept={-0.7} />
          <feFuncB type="linear" slope={2.4} intercept={-0.7} />
          <feFuncA type="linear" slope={0} intercept={0.4} />
        </feComponentTransfer>
        <feComposite in="na" in2="SourceAlpha" operator="in" result="nm" />
        <feBlend in="nm" in2="rgb" mode="overlay" />
      </filter>
    </svg>
  );
}
