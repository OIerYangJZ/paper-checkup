/** Brand mark: a small dependency graph (definitions → lemma → theorem) with a check on the result. */
export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect x="0.5" y="0.5" width="31" height="31" rx="8" fill="#1c2b4b" />
      <path d="M9 9.5 L16 16 M23 9.5 L16 16 M16 16 L16 22" stroke="#8fa6e6" strokeWidth="1.6" fill="none" strokeLinecap="round" />
      <circle cx="9" cy="9.5" r="2.6" fill="#5eead4" />
      <circle cx="23" cy="9.5" r="2.6" fill="#93c5fd" />
      <circle cx="16" cy="16" r="2.6" fill="#a5b4fc" />
      <circle cx="16" cy="23.2" r="3.4" fill="#fb923c" />
      <path d="M14.4 23.3 l1.1 1.1 l2.1 -2.2" stroke="#fff" strokeWidth="1.3" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
