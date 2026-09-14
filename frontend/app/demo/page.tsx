import { Container } from "@/components/layout/container";
import { MemoView } from "@/components/memo/memo-view";
import demo from "@/fixtures/demo-memo.json";
import { MemoResponseZ } from "@/lib/api/memo-schema";

export default function DemoPage() {
  return (
    <Container className="py-8">
      <p className="mb-4 rounded border p-3 text-sm">
        Offline demonstration using fictional research notes. No live data or API calls.
      </p>
      <MemoView response={MemoResponseZ.parse(demo)} />
    </Container>
  );
}
