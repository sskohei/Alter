import { ConversationDetail } from "../../../../components/conversation-detail";
export default async function Conversation({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <ConversationDetail key={id} id={id} />;
}
