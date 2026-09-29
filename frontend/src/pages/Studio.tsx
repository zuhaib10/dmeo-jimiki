import { useQuery } from "@tanstack/react-query";
import { Images } from "lucide-react";
import { Navigate } from "react-router-dom";
import { api } from "../api/client";
import { Empty } from "../components/ui";

/** "Image Studio" opens the product currently being worked on (or the most recent one). */
export default function Studio() {
  const { data, isLoading } = useQuery({ queryKey: ["studio"], queryFn: api.studioCurrent });
  if (isLoading) return <div className="p-6 text-sm text-muted">Loading…</div>;
  if (data?.id) return <Navigate to={`/products/${data.id}`} replace />;
  return (
    <div className="p-6">
      <Empty icon={<Images className="size-5" />} title="No products yet">
        Drop jewellery photographs into the raw images folder. The studio opens the product being processed.
      </Empty>
    </div>
  );
}
