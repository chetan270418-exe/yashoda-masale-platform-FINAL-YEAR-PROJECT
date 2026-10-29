import React from "react";

export default function StorePage() {
  return (
    <main style={{ padding: "2rem", maxWidth: "1200px", margin: "0 auto" }}>
      <header style={{ marginBottom: "2rem" }}>
        <h1 style={{ color: "#7B1113" }}>Yashoda Masale Spices</h1>
        <p style={{ color: "#555" }}>
          Pure, authentic stone-ground spices crafted with traditional recipes from Maharashtra.
        </p>
      </header>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "1.5rem" }}>
        <div style={{ border: "1px solid #e0e0e0", borderRadius: "8px", padding: "1.5rem" }}>
          <h3>Garam Masala (250g)</h3>
          <p>Hand-roasted blend of 16 aromatic whole spices.</p>
          <strong>₹120.00</strong>
        </div>
        <div style={{ border: "1px solid #e0e0e0", borderRadius: "8px", padding: "1.5rem" }}>
          <h3>Turmeric Powder / Haldi (500g)</h3>
          <p>Salem rhizomes with minimum 3.0% curcumin guarantee.</p>
          <strong>₹180.00</strong>
        </div>
      </div>
    </main>
  );
}
