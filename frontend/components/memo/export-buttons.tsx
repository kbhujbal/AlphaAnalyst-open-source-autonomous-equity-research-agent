import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";

interface ExportButtonsProps {
  pdfHref: string | null;
  excelHref: string | null;
}

export function ExportButtons({ pdfHref, excelHref }: ExportButtonsProps) {
  return (
    <div className="space-y-2 print:hidden">
      {pdfHref ? (
        <Button variant="outline" className="w-full" asChild>
          <a href={pdfHref} target="_blank" rel="noopener noreferrer">
            <Download className="mr-2 h-4 w-4" aria-hidden="true" />Download PDF
          </a>
        </Button>
      ) : <p className="text-sm text-muted-foreground">PDF export unavailable for this report.</p>}
      {excelHref ? (
        <Button variant="outline" className="w-full" asChild>
          <a href={excelHref} target="_blank" rel="noopener noreferrer">
            <Download className="mr-2 h-4 w-4" aria-hidden="true" />Download Excel snapshot
          </a>
        </Button>
      ) : <p className="text-sm text-muted-foreground">Excel export unavailable for this report.</p>}
    </div>
  );
}
