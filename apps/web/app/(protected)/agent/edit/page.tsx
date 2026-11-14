import Link from "next/link";
import { requireProfile } from "../../../../lib/api-server";
import { ownedAgentState } from "../../../../lib/owned-agent";
import { AgentEditor } from "../../../../components/agent-editor";
import { ResourceError } from "../../../../components/resource-error";
export default async function EditAgentPage() {
  const profile = await requireProfile();
  const state = await ownedAgentState();
  if (!state.ok) return <ResourceError />;
  if (!state.data)
    return (
      <>
        <h1>編集するAI分身がありません</h1>
        <Link className="button" href="/agent/new">
          AI分身を作成する
        </Link>
      </>
    );
  return <AgentEditor owner={profile.id} initial={state.data} />;
}
