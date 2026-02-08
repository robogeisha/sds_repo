# actions.py
from typing import Any, Dict, List, Text

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.forms import FormValidationAction
from rasa_sdk.events import SlotSet, ActiveLoop, FollowupAction


class ValidateWifiMainForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_wifi_main_form"

    async def required_slots(
        self,
        slots_mapped_in_domain: List[Text],
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> List[Text]:
        req = ["device_type", "loads_example"]
        loads = tracker.get_slot("loads_example")

        # If example.com loads, ask: scope + symptom style + other devices
        if loads is True:
            req += ["scope_issue", "random_failures", "other_devices"]

        # If example.com does NOT load, ask captive portal
        elif loads is False:
            req += ["sees_login"]

        # Ask router access regardless
        req += ["can_restart_router"]

        return req

    def validate_device_type(
        self,
        value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        if value in ("phone", "computer"):
            return {"device_type": value}
        dispatcher.utter_message(text="Just say **phone** or **computer**.")
        return {"device_type": None}


    def validate_scope_issue(
        self,
        value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        text = (value or "").strip().lower()
        if not text:
            return {"scope_issue": None}

        # Interpret common answers
        if any(k in text for k in ["everything", "all", "every", "whole", "all sites", "all apps"]):
            return {"scope_issue": "everything"}
        if any(k in text for k in ["one", "only", "just", "youtube", "spotify", "discord", "instagram", "tiktok", "one site", "one app"]):
            return {"scope_issue": "one"}

        # If unclear, ask again
        dispatcher.utter_message(text="Just say **everything** or **one app/site**.")
        return {"scope_issue": None}


# ============================================================
# Main router for advice
# ============================================================
class ActionRouteAdvice(Action):
    def name(self) -> Text:
        return "action_route_advice"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ):
        attempt = int(tracker.get_slot("attempt_count") or 0)
        device = tracker.get_slot("device_type")

        loads = tracker.get_slot("loads_example")
        scope_issue = tracker.get_slot("scope_issue")  # NEW
        random_failures = tracker.get_slot("random_failures")
        other_devices = tracker.get_slot("other_devices")
        sees_login = tracker.get_slot("sees_login")
        can_restart_router = tracker.get_slot("can_restart_router")

        last_advice = tracker.get_slot("last_advice")

        latest_text = (tracker.latest_message.get("text") or "").strip()
        latest_lower = latest_text.lower()



        if last_advice == "ask_platform_for_dns":
            detected = None

            if any(k in latest_lower for k in ["linux", "ubuntu", "debian", "arch", "fedora", "mint"]):
                detected = "linux"
            elif any(k in latest_lower for k in ["windows", "win10", "win 10", "win11", "win 11"]) or latest_lower in {"win"}:
                detected = "windows"
            elif any(k in latest_lower for k in ["macos", "osx", "mac book", "macbook", "mac"]):
                detected = "macos"

            if not detected:
                dispatcher.utter_message("Just say **Windows**, **macOS**, or **Linux**.")
                return [
                    SlotSet("platform", None),
                    SlotSet("last_advice", "ask_platform_for_dns"),
                    SlotSet("resolved", None),
                ]

            return [
                SlotSet("platform", detected),
                SlotSet("resolved", None),
                FollowupAction("action_flush_dns_for_platform"),
            ]

        # ----------------
        # Branch A: example.com loads (diagnostic path)
        # ----------------
        if loads is True:
            # attempt == 0: do the logical split first
            if attempt == 0:
                # If it's ONLY one app/site, do device/app troubleshooting first
                if scope_issue == "one":
                    if device == "computer":
                        dispatcher.utter_message(
                            "If it’s only one app/site, it’s usually not the Wi-Fi.\n"
                            "Try another browser, disable vpn if you have it\n"
                            "Then test again."
                        )
                        return [
                            SlotSet("last_advice", "loads_one_site_computer_basic"),
                            FollowupAction("action_after_advice"),
                        ]
                    else:
                        dispatcher.utter_message(
                      
                            "Force close the app and reopen\n"
                            "Toggle Wi-Fi.\n"
                            "Then test again."
                        )
                        return [
                            SlotSet("last_advice", "loads_one_site_phone_basic"),
                            FollowupAction("action_after_advice"),
                        ]

                # Otherwise treat as "everything" and use network-quality logic
                if other_devices is True:
                    dispatcher.utter_message(
                        "So internet works, but it’s degraded. If other devices are streaming or downloading, pause them for a minute, then test again."
                    )
                    return [
                        SlotSet("last_advice", "loads_pause_others"),
                        FollowupAction("action_after_advice"),
                    ]

                if random_failures is True:
                    dispatcher.utter_message(
                        "Random drops usually means Wi-Fi quality. Move closer to the router, and if you see 2.4G/5G, switch bands and test again."
                    )
                    return [
                        SlotSet("last_advice", "loads_switch_band"),
                        FollowupAction("action_after_advice"),
                    ]

                dispatcher.utter_message(
                    "If it’s consistently slow across everything: disable VPN if you have it, then toggle Wi-Fi on and off and test again."
                )
                return [
                    SlotSet("last_advice", "loads_toggle_wifi"),
                    FollowupAction("action_after_advice"),
                ]

            # attempt == 1: forget/rejoin
            elif attempt == 1:
                dispatcher.utter_message("Next: forget the Wi-Fi network, reconnect, then test again.")
                return [
                    SlotSet("last_advice", "loads_forget_rejoin"),
                    FollowupAction("action_after_advice"),
                ]

            # attempt >= 2: router restart if possible
            else:
                if can_restart_router is True:
                    dispatcher.utter_message(
                        "Restart the router or modem — unplug 10 seconds, plug in, wait ~1 minute, then test."
                    )
                    return [
                        SlotSet("last_advice", "loads_restart_router"),
                        FollowupAction("action_after_advice"),
                    ]

                dispatcher.utter_message(
                    "Since you don’t have router access: this is likely upstream congestion or network policy. Try another Wi-Fi or a hotspot to confirm."
                )
                return [
                    SlotSet("last_advice", "loads_no_router"),
                    FollowupAction("action_after_advice"),
                ]

        # ----------------
        # Branch B: example.com does NOT load
        # ----------------
        if sees_login is True:
            dispatcher.utter_message(
                "That’s probably a captive portal. Open the login/terms page, accept it, then test again."
            )
            return [
                SlotSet("last_advice", "portal"),
                FollowupAction("action_after_advice"),
            ]

        # ---------------------------------------------------------
        # No captive portal: tier ladder (no-load path)
        # ---------------------------------------------------------
        if attempt == 0:
            if device == "phone":
                dispatcher.utter_message("Level 1 (phone): airplane mode ON for 5 seconds, then OFF.")
                return [
                    SlotSet("last_advice", "tier_airplane"),
                    FollowupAction("action_after_advice"),
                ]

            dispatcher.utter_message(
                "Turn Wi-Fi off and on. If you can: disable/enable the network adapter, then test again."
            )
            return [
                SlotSet("last_advice", "tier_toggle_adapter"),
                FollowupAction("action_after_advice"),
            ]

        elif attempt == 1:
            if can_restart_router is True:
                dispatcher.utter_message(
                    "Restart the router/modem. Unplug 10 seconds, plug back in, wait about a minute, then test."
                )
                return [
                    SlotSet("last_advice", "tier_restart_router"),
                    FollowupAction("action_after_advice"),
                ]

            dispatcher.utter_message(
                "Forget this Wi-Fi network, reconnect, and re-enter the password, then test."
            )
            return [
                SlotSet("last_advice", "tier_forget_rejoin_no_router"),
                FollowupAction("action_after_advice"),
            ]

        else:
            if device == "computer":
                dispatcher.utter_message(
                    "We’ll reset your IP + DNS. Which platform are you on: **Windows**, **macOS**, or **Linux**?"
                )
                return [
                    SlotSet("last_advice", "ask_platform_for_dns"),
                    SlotSet("platform", None),
                    SlotSet("resolved", None),
                    ActiveLoop(None),
                ]

            dispatcher.utter_message(
                "Try **Reset Network Settings** (this clears saved Wi-Fi + Bluetooth). Then reconnect to Wi-Fi and test again."
            )
            return [
                SlotSet("last_advice", "tier_phone_reset_network"),
                FollowupAction("action_after_advice"),
            ]


# ============================================================
# After advice -> ask resolved
# ============================================================
class ActionAfterAdvice(Action):
    def name(self) -> Text:
        return "action_after_advice"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]):
        return [
            SlotSet("resolved", None),
            ActiveLoop(None),
            FollowupAction("wifi_resolved_form"),
        ]


# ============================================================
# Increment attempts or finish
# ============================================================
class ActionIncrementAttemptsOrFinish(Action):
    def name(self) -> Text:
        return "action_increment_attempts_or_finish"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]):
        resolved = tracker.get_slot("resolved")
        attempt = int(tracker.get_slot("attempt_count") or 0)
        last_advice = tracker.get_slot("last_advice")

        if last_advice == "ask_platform_for_dns":
            dispatcher.utter_message("Just say **Windows**, **macOS**, or **Linux**.")
            return [SlotSet("resolved", None)]

        if resolved is True:
            dispatcher.utter_message("LET’S GO. Honestly... a noob problem.")
            return [
                SlotSet("attempt_count", 0),
                SlotSet("resolved", None),
                SlotSet("last_advice", None),
                SlotSet("platform", None),
                SlotSet("scope_issue", None),
            ]

        if resolved is False and last_advice == "tier_forget_rejoin_dns":
            dispatcher.utter_message(
                "Yeah… if IP + DNS refresh didn’t help, you’re cooked. Call your network-admin territory."
            )
            return [
                SlotSet("attempt_count", 0),
                SlotSet("resolved", None),
                SlotSet("last_advice", None),
                SlotSet("platform", None),
                SlotSet("scope_issue", None),
            ]

        if last_advice == "portal":
            dispatcher.utter_message("So the portal wasn’t it.")
            return [
                SlotSet("resolved", None),
                SlotSet("sees_login", False),
                SlotSet("last_advice", None),
                SlotSet("platform", None),
                FollowupAction("action_route_advice"),
            ]

        attempt += 1

        if attempt > 2:
            dispatcher.utter_message("I don't know mate, you’re cooked. Contact your network admin.")
            return [
                SlotSet("attempt_count", 0),
                SlotSet("resolved", None),
                SlotSet("last_advice", None),
                SlotSet("platform", None),
                SlotSet("scope_issue", None),
            ]

        dispatcher.utter_message("Alright. That didn’t work.")
        return [
            SlotSet("attempt_count", attempt),
            SlotSet("resolved", None),
            FollowupAction("action_route_advice"),
        ]


# ============================================================
# Platform-specific IP renew + DNS flush
# ============================================================
class ActionFlushDnsForPlatform(Action):
    def name(self) -> Text:
        return "action_flush_dns_for_platform"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]):
        platform = tracker.get_slot("platform")

        if platform == "windows":
            dispatcher.utter_message(
                "to Renew IP: `ipconfig /release` then `ipconfig /renew`\n"
                "to Flush DNS: `ipconfig /flushdns`"
            )
        elif platform == "linux":
            dispatcher.utter_message(
               
                "to Renew IP: `sudo dhclient -r` then `sudo dhclient`\n"
                "to Flush DNS: `sudo resolvectl flush-caches`"
            )
        elif platform == "macos":
            dispatcher.utter_message(

                "to Renew IP: toggle Wi-Fi off and on, or renew DHCP lease in Network settings\n"
                "to Flush DNS: `sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder`"
            )
        else:
            dispatcher.utter_message("Which platform are you on: **Windows**, **macOS**, or **Linux**?")
            return [
                SlotSet("platform", None),
                SlotSet("last_advice", "ask_platform_for_dns"),
                SlotSet("resolved", None),
            ]

        return [
            SlotSet("resolved", None),
            SlotSet("last_advice", "tier_forget_rejoin_dns"),
            ActiveLoop(None),
            FollowupAction("wifi_resolved_form"),
        ]


# ============================================================
# Reset action
# ============================================================
class ActionResetTroubleshoot(Action):
    def name(self) -> Text:
        return "action_reset_troubleshoot"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]):
        return [
            SlotSet("attempt_count", 0),
            SlotSet("device_type", None),
            SlotSet("loads_example", None),
            SlotSet("scope_issue", None),
            SlotSet("random_failures", None),
            SlotSet("other_devices", None),
            SlotSet("sees_login", None),
            SlotSet("can_restart_router", None),
            SlotSet("resolved", None),
            SlotSet("last_advice", None),
            SlotSet("platform", None),
            ActiveLoop(None),
        ]


