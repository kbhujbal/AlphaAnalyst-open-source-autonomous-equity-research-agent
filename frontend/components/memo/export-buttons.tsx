"use client";

import { Download } from "lucide-react";
import { useState } from "react";

import { LoadingSpinner } from "@/components/ui/loading-spinner";
import { Button } from "@/components/ui/button";

interface ExportButtonsProps {
  pdfHref: string;
  excelHref: string;
}

const SPINNER_MS = 2000;

export function ExportButtons({ pdfHref, excelHref }: ExportButtonsProps) {
  const [pending, setPending] = useState<"pdf" | "excel" | null>(null);

  function startSpinner(kind: "pdf" | "excel") {
    setPending(kind);
    window.setTimeout(() => {
      setPending((current) => (current === kind ? null : current));
    }, SPINNER_MS);
  }

  return (
    <div className="space-y-2 print:hidden">
      <Button
        variant="outline"
        className="w-full"
        asChild
        disabled={pending === "pdf"}
      >
        <a
          href={pdfHref}
          target="_blank"
          rel="noopener noreferrer"
          onClick={() => startSpinner("pdf")}
        >
          {pending === "pdf" ? (
            <LoadingSpinner className="mr-2" />
          ) : (
            <Download className="mr-2 h-4 w-4" aria-hidden="true" />
          )}
          Download PDF
        </a>
      </Button>
      <Button
        variant="outline"
        className="w-full"
        asChild
        disabled={pending === "excel"}
      >
        <a
          href={excelHref}
          target="_blank"
          rel="noopener noreferrer"
          onClick={() => startSpinner("excel")}
        >
          {pending === "excel" ? (
            <LoadingSpinner className="mr-2" />
          ) : (
            <Download className="mr-2 h-4 w-4" aria-hidden="true" />
          )}
          Download Excel
        </a>
      </Button>
    </div>
  );
}
