import React from "react";

export default function ProductDetailPage({ params }: { params: { slug: string } }) {
  return (
    <main style={{ padding: "2rem", maxWidth: "800px", margin: "0 auto" }}>
      <h1>Product: {params.slug}</h1>
      <p>Authentic quality guarantee with batch traceability.</p>
    </main>
  );
}
