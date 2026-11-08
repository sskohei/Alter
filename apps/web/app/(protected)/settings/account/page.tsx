import { cookies } from "next/headers";
import { requireProfile, serverApi } from "../../../../lib/api-server";
import { proofCookie, validProof } from "../../../../lib/reauth-proof";
import { AccountSettings } from "../../../../components/account-settings";
export default async function AccountPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const profile = await requireProfile();
  const { supabase } = await serverApi();
  const token = (await supabase.auth.getSession()).data.session?.access_token;
  const verified =
    !!token &&
    validProof((await cookies()).get(proofCookie)?.value, token, profile.id);
  return (
    <AccountSettings
      initialProfile={profile}
      reauthenticated={verified}
      reauthError={!!(await searchParams).error}
    />
  );
}
