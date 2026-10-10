#if UNITY_EDITOR || (DEVELOPMENT_BUILD && AI_TEST_AGENT_BRIDGE)
using System.Collections.Generic;
using UnityEngine;

namespace AiTestAgent.Bridge
{
    public static class UgUiSnapshot
    {
        // Opt-in UI reads only. Coordinates use Android screenshot's top-left origin.
        public static WindowSnapshot ReadActive()
        {
            var items = new List<UiElementSnapshot>();
            foreach (RectTransform rect in Object.FindObjectsOfType<RectTransform>())
            {
                if (!rect.gameObject.activeInHierarchy) continue;
                Canvas canvas = rect.GetComponentInParent<Canvas>();
                if (canvas == null || !canvas.isActiveAndEnabled) continue;
                Camera camera = canvas.renderMode == RenderMode.ScreenSpaceOverlay ? null : canvas.worldCamera;
                if (canvas.renderMode != RenderMode.ScreenSpaceOverlay && camera == null) continue;
                var corners = new Vector3[4];
                rect.GetWorldCorners(corners);
                float minX = float.PositiveInfinity, minY = float.PositiveInfinity;
                float maxX = float.NegativeInfinity, maxY = float.NegativeInfinity;
                foreach (Vector3 corner in corners)
                {
                    Vector2 point = RectTransformUtility.WorldToScreenPoint(camera, corner);
                    minX = Mathf.Min(minX, point.x); minY = Mathf.Min(minY, point.y);
                    maxX = Mathf.Max(maxX, point.x); maxY = Mathf.Max(maxY, point.y);
                }
                items.Add(new UiElementSnapshot { path = HierarchyPath(rect), x = minX,
                    y = Screen.height - maxY, width = maxX - minX, height = maxY - minY });
            }
            return new WindowSnapshot { available = true, items = items.ToArray() };
        }

        private static string HierarchyPath(Transform transform)
        {
            var segments = new List<string>();
            while (transform != null)
            {
                segments.Add(transform.name + "[" + transform.GetSiblingIndex() + "]");
                transform = transform.parent;
            }
            segments.Reverse();
            return "/" + string.Join("/", segments.ToArray());
        }
    }
}
#endif
