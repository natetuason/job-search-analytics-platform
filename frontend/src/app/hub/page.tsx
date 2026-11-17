import { Suspense } from "react";
import { Hub } from "@/components/Hub";
import { Loading } from "@/components/ui";

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <Hub />
    </Suspense>
  );
}
