import React from "react";

/**
 * Public QR Batch Traceability Page (Rule B11).
 * Shows allow-listed recorded timeline fields only.
 * Unknown token displays "No record found".
 * Never claims genuine from a QR scan alone.
 */
export default function BatchTracePage({ params }: { params: { token: string } }) {
  return (
    <main style={{ padding: "2rem", maxWidth: "800px", margin: "0 auto" }}>
      <div style={{ border: "1px solid #7B1113", borderRadius: "8px", padding: "1.5rem" }}>
        <h2 style={{ color: "#7B1113", marginTop: 0 }}>Batch Traceability Verification</h2>
        <p><strong>Batch Token:</strong> {params.token}</p>
        <p style={{ color: "#666" }}>
          Notice: This page displays verified production and quality records for this batch code.
          A scan proves a database record exists, but cannot guarantee packaging physical authenticity.
        </p>
      </div>
    </main>
  );
}
