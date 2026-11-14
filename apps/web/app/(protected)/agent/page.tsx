import Link from "next/link";
import { requireProfile } from "../../../lib/api-server";
import { ownedAgentState } from "../../../lib/owned-agent";
import { toForm } from "../../../lib/agent-form";
import { AgentPreview } from "../../../components/agent-preview";
import { ResourceError } from "../../../components/resource-error";
export default async function AgentPage() {
  await requireProfile();
  const state = await ownedAgentState();
  if (!state.ok) return <ResourceError />;
  if (!state.data)
    return (
      <>
        <h1>あなたのAI分身</h1>
        <section className="card">
          <h2>まだAI分身がいません</h2>
          <p>公開してよい人格を入力し、本人が承認してから利用します。</p>
          <Link className="button" href="/onboarding">
            作成ガイドをはじめる
          </Link>
          <p>
            <Link href="/agent/new">手動で入力する</Link>
          </p>
        </section>
      </>
    );
  const { agent, revision } = state.data;
  return (
    <>
      <p className="eyebrow">AI分身 / 本人の発言ではありません</p>
      <h1>{agent.name}</h1>
      <section className="card">
        <h2>
          {revision.approved_at
            ? `承認済み · 版 ${revision.version}`
            : `未承認の下書き · 版 ${revision.version}`}
        </h2>
        {!revision.approved_at && (
          <p className="notice">
            この版はまだ交流に使用できません。
            {agent.approved_profile_version
              ? `交流に使用する承認済み版は ${agent.approved_profile_version} です。`
              : "編集画面で内容を確認して承認してください。"}
          </p>
        )}
        <p>
          自律交流: {agent.settings?.autonomy_enabled ? "ON" : "OFF"} /
          Discord公開: {agent.settings?.discord_publish ? "ON" : "OFF"}
        </p>
        <AgentPreview values={toForm(agent, revision)} />
        <Link className="button" href="/agent/edit">
          人格を編集・承認する
        </Link>
      </section>
    </>
  );
}
