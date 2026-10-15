package com.wealthsense.app.utils

import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import com.wealthsense.app.data.preferences.AppIcon

object IconSwitchingUtils {
    fun switchAppIcon(context: Context, targetIcon: AppIcon) {
        val packageManager = context.packageManager
        val packageName = context.packageName

        val iconComponents = mapOf(
            AppIcon.ORIGINAL to "$packageName.MainActivityOriginal",
            AppIcon.ANARCHY to "$packageName.MainActivityAnarchy",
            AppIcon.ZENITH to "$packageName.MainActivityZenith",
            AppIcon.MONOCHROME to "$packageName.MainActivityMonochrome"
        )

        iconComponents.forEach { (icon, componentName) ->
            val component = ComponentName(context, componentName)
            val newState = if (icon == targetIcon) {
                PackageManager.COMPONENT_ENABLED_STATE_ENABLED
            } else {
                PackageManager.COMPONENT_ENABLED_STATE_DISABLED
            }
            
            // Only update if state is different to avoid system overhead and potential lag
            if (packageManager.getComponentEnabledSetting(component) != newState) {
                packageManager.setComponentEnabledSetting(
                    component,
                    newState,
                    PackageManager.DONT_KILL_APP
                )
            }
        }
    }
}
