#!/bin/bash
# Quick health check: VM running, static IP in use (no idle IP charges), bot active.
#
# Usage (from Mac):
#   bash deploy/check_gcp_health.sh [VM_NAME] [ZONE] [STATIC_IP_NAME] [REGION]
#
# Defaults match trading-bot-vm in us-west1.

set -euo pipefail

VM_NAME="${1:-trading-bot-vm}"
ZONE="${2:-us-west1-b}"
IP_NAME="${3:-trading-bot-static-ip}"
REGION="${4:-us-west1}"
PROJECT="$(gcloud config get-value project 2>/dev/null)"

if [[ -z "$PROJECT" || "$PROJECT" == "(unset)" ]]; then
  echo "Error: gcloud project not set." >&2
  exit 1
fi

echo "==> GCP trading bot health ($PROJECT)"
echo ""

VM_STATUS="$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --format='get(status)' 2>/dev/null || echo MISSING)"
NAT_IP="$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --format='get(networkInterfaces[0].accessConfigs[0].natIP)' 2>/dev/null || true)"
IP_STATUS="$(gcloud compute addresses describe "$IP_NAME" --region="$REGION" --project="$PROJECT" --format='get(status)' 2>/dev/null || echo MISSING)"
IP_ADDR="$(gcloud compute addresses describe "$IP_NAME" --region="$REGION" --project="$PROJECT" --format='get(address)' 2>/dev/null || true)"

echo "VM:         $VM_NAME ($ZONE) → $VM_STATUS"
echo "External:   ${NAT_IP:-n/a}"
echo "Static IP:  $IP_NAME → $IP_STATUS ($IP_ADDR)"
echo ""

FAIL=0

if [[ "$VM_STATUS" != "RUNNING" ]]; then
  echo "⚠️  VM is not RUNNING. Start it to avoid static IP idle charges:"
  echo "    gcloud compute instances start $VM_NAME --zone=$ZONE"
  FAIL=1
fi

if [[ "$IP_STATUS" == "RESERVED" ]]; then
  echo "⚠️  Static IP is RESERVED but not attached (~\$3/month). Either:"
  echo "    1) Start the VM and attach this IP, or"
  echo "    2) Delete the reservation if you no longer need a fixed IP:"
  echo "       gcloud compute addresses delete $IP_NAME --region=$REGION"
  FAIL=1
elif [[ "$IP_STATUS" == "IN_USE" ]]; then
  echo "✅ Static IP IN_USE — no idle IP charge while VM runs."
fi

if [[ -n "$NAT_IP" && -n "$IP_ADDR" && "$NAT_IP" != "$IP_ADDR" ]]; then
  echo "⚠️  VM NAT IP ($NAT_IP) does not match reserved static IP ($IP_ADDR)."
  FAIL=1
fi

echo ""
echo "Remote services (SSH):"
if [[ "$VM_STATUS" == "RUNNING" ]]; then
  gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --command='
    echo "  bot-v2:        $(systemctl is-active bot-v2 2>/dev/null || echo unknown) (enabled: $(systemctl is-enabled bot-v2 2>/dev/null || echo unknown))"
    echo "  kite-auth:     $(systemctl is-active kite-auth.timer 2>/dev/null || echo unknown) (enabled: $(systemctl is-enabled kite-auth.timer 2>/dev/null || echo unknown))"
    systemctl list-timers kite-auth.timer --no-pager 2>/dev/null | tail -n +2 | head -1 | sed "s/^/  next login:  /"
  ' 2>/dev/null || echo "  (SSH check skipped)"
fi

echo ""
if [[ "$FAIL" -eq 0 ]]; then
  echo "✅ All checks passed. Keep VM running 24/7 for \$0 static IP."
  exit 0
else
  echo "❌ Fix issues above to stay on free tier and avoid IP charges."
  exit 1
fi
