import { useState } from 'react';
import type { PendingConfirmation } from '../sidepanel/turns';

interface Props {
  confirmation: PendingConfirmation;
  onDecide(approved: boolean): void;
}

/** Asks the user to approve a high-impact action before it happens. */
export function ConfirmationCard({ confirmation, onDecide }: Props) {
  const [sent, setSent] = useState(false);
  const decide = (approved: boolean): void => {
    setSent(true);
    onDecide(approved);
  };
  return (
    <div className="confirmation-card" role="alertdialog" aria-labelledby={`c-${confirmation.requestId}`}>
      <p className="confirmation-title">Approve this step?</p>
      <p className="confirmation-action" id={`c-${confirmation.requestId}`}>
        {confirmation.action}
      </p>
      <p className="confirmation-reason">{confirmation.reason}</p>
      <div className="confirmation-actions">
        <button type="button" className="button" disabled={sent} onClick={() => decide(false)}>
          Don't do it
        </button>
        <button type="button" className="button button-primary" disabled={sent} onClick={() => decide(true)}>
          Approve
        </button>
      </div>
    </div>
  );
}
