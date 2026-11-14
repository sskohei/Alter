"use client";
import { useRouter } from "next/navigation";
import { Button } from "./ui/button";
export function ResourceError() {
  const router = useRouter();
  return (
    <section className="card" role="alert">
      <h1>AI分身を読み込めませんでした</h1>
      <p>時間をおいて、もう一度お試しください。</p>
      <Button onClick={() => router.refresh()}>再試行</Button>
    </section>
  );
}
