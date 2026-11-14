// Editor/instrument style overlay around the hero object: ticks, bearings,
// rotating arc and two orbiting agents. Purely decorative.
const C = 500;
const R = 470;
const point = (r: number, deg: number) => {
  const a = ((deg - 90) * Math.PI) / 180;
  return [C + r * Math.cos(a), C + r * Math.sin(a)].map((n) => Number(n.toFixed(1)));
};
const arc = (r: number, from: number, to: number) => {
  const [x0, y0] = point(r, from);
  const [x1, y1] = point(r, to);
  return `M${x0} ${y0} A${r} ${r} 0 ${to - from > 180 ? 1 : 0} 1 ${x1} ${y1}`;
};
const ANGLES = Array.from({ length: 72 }, (_, i) => i * 5);

export function Hud() {
  const [ex, ey] = point(R - 80, 40);
  return (
    <svg className="lp-hud" viewBox="0 0 1000 1000" aria-hidden="true" focusable="false">
      <circle className="ln" cx={C} cy={C} r={R} />
      {ANGLES.map((a) => {
        const long = a % 30 === 0;
        const [x1, y1] = point(R, a);
        const [x2, y2] = point(R - (long ? 24 : 10), a);
        return <line key={a} className="tk" x1={x1} y1={y1} x2={x2} y2={y2} />;
      })}
      {ANGLES.filter((a) => a % 30 === 0).map((a) => {
        const [x, y] = point(R - 48, a);
        return <text key={a} className="lb" x={x} y={y} textAnchor="middle" dominantBaseline="middle">{String(a).padStart(3, "0")}</text>;
      })}
      <circle className="ln" cx={C} cy={C} r={R - 80} />
      <g className="spin-r"><circle className="ln dash" cx={C} cy={C} r={330} /></g>
      <g className="spin"><path className="ac" d={arc(R - 80, -20, 40)} /><circle className="dot" cx={ex} cy={ey} r={6} /></g>
      <g className="orbit">
        <circle className="dot" cx={C} cy={170} r={8} /><text className="lb ag" x={518} y={166}>AGENT_A</text>
        <circle className="dot hollow" cx={C} cy={830} r={8} /><text className="lb ag" x={518} y={834}>AGENT_B</text>
      </g>
      <path className="ln" d="M20 80V20H80M920 20H980V80M980 920V980H920M80 980H20V920" />
      <text className="lb" x={24} y={1016}>R 0470 / θ 030°</text>
      <text className="lb" x={980} y={-4} textAnchor="end">ALT-001 / AGENT PAIR</text>
    </svg>
  );
}

const MARKS = Array.from({ length: 20 }, (_, i) => i * 100);
export function Rulers() {
  return (
    <>
      <div className="lp-ruler rx" aria-hidden="true">{MARKS.map((m) => <span key={m} style={{ left: m }}>{m}</span>)}</div>
      <div className="lp-ruler ry" aria-hidden="true">{MARKS.map((m) => <span key={m} style={{ top: m }}>{m}</span>)}</div>
      <div className="lp-ruler rc" aria-hidden="true" />
    </>
  );
}
