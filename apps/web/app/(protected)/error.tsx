"use client";
export default function ErrorPage({ reset }: { reset: () => void }) { return <section role="alert" className="card"><h1>読み込めませんでした</h1><p>時間をおいて、もう一度お試しください。</p><button onClick={reset}>再試行</button></section>; }
