import { redirect } from "next/navigation";
import { requireProfile } from "../../../../lib/api-server";
import { ownedAgent } from "../../../../lib/owned-agent";
import { AgentEditor } from "../../../../components/agent-editor";
import { ResourceError } from "../../../../components/resource-error";
export default async function NewAgentPage() {
  const profile = await requireProfile();
  let agent;
  try {
    agent = await ownedAgent();
  } catch {
    return <ResourceError />;
  }
  if (agent) redirect("/agent/edit");
  return <AgentEditor owner={profile.id} />;
}
