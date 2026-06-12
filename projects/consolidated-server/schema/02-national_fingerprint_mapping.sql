-- The biometric identity table the SQLMesh patient model joins. It is NOT in the
-- production consolidated_db dump (pending delivery from CHARESS), so we create it
-- empty here on first boot. The patient model's national_id overlay stays inert
-- (no national_id attached) until this is populated by the real fingerprint feed.
CREATE TABLE IF NOT EXISTS national_fingerprint_mapping (
  id INT AUTO_INCREMENT PRIMARY KEY,
  mspp_code VARCHAR(20) NOT NULL,
  patient_id INT NOT NULL,
  national_id VARCHAR(20),
  m2sys_unique_id VARCHAR(20),
  identify_score INT,
  statut VARCHAR(30),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
