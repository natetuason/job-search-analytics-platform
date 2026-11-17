import { Suspense } from "react";
import { JobsList } from "@/components/JobsList";
import { Loading } from "@/components/ui";

export default function Home() {
  return (
    <Suspense fallback={<Loading />}>
      <JobsList />
    </Suspense>
  );
}
