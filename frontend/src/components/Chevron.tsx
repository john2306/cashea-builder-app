/** Chevron que rota al expandir/contraer (razonamiento, tools). */
export function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      className={`chev ${open ? "open" : ""}`}
      viewBox="0 0 24 24"
      width="14"
      height="14"
      aria-hidden="true"
    >
      <path
        d="M9 6l6 6-6 6"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
