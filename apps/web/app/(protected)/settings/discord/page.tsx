import { DiscordSettings } from "../../../../components/discord-settings";
export default async function DiscordPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; linked?: string }>;
}) {
  const params = await searchParams;
  return (
    <DiscordSettings errorCode={params.error} linked={params.linked === "1"} />
  );
}
