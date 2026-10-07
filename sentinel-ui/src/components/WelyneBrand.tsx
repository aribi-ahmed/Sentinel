import './WelyneBrand.css';

/**
 * The Welyne mark, rebuilt as inline SVG.
 *
 * Inline rather than an image file so it stays crisp at any size, needs no
 * asset pipeline, and cannot 404. The geometry is a hand-rebuild of the supplied
 * artwork — if the official vector file is available it should replace the paths
 * below rather than being linked alongside them.
 */
export function WelyneMark({ size = 30 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 384 384"
      role="img"
      aria-label="Welyne"
      className="welyne-mark"
    >
      <defs>
        <clipPath id="welyne-disc">
          <circle cx="192" cy="192" r="186" />
        </clipPath>
        <linearGradient id="welyne-fill" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#FB8B3C" />
          <stop offset="52%" stopColor="#F26418" />
          <stop offset="100%" stopColor="#DC3C02" />
        </linearGradient>
      </defs>

      <g clipPath="url(#welyne-disc)">
        <circle cx="192" cy="192" r="186" fill="url(#welyne-fill)" />

        {/* Three slashes cut the disc into shards. Tapered rather than parallel,
            which is what gives the mark its rotation. */}
        <g transform="rotate(-41 192 192)">
          <polygon points="-240,68 620,44 620,50 -240,98" fill="#fff" />
          <polygon points="-240,172 620,152 620,160 -240,202" fill="#fff" />
          <polygon points="-240,278 620,260 620,268 -240,304" fill="#fff" />
        </g>
      </g>
    </svg>
  );
}

/**
 * The Welyne wordmark and logo, linking out to the company site.
 *
 * `rel="noopener noreferrer"` because it is a cross-origin `target="_blank"`
 * link: without `noopener` the opened page gets a handle on this one through
 * `window.opener`.
 */
export function WelyneBrand() {
  return (
    <a
      className="welyne-brand focus-ring"
      href="https://welyne.com"
      target="_blank"
      rel="noopener noreferrer"
      title="Welyne — welyne.com"
      data-testid="link-welyne"
    >
      <WelyneMark size={30} />
      <span className="welyne-wordmark">Welyne</span>
    </a>
  );
}
