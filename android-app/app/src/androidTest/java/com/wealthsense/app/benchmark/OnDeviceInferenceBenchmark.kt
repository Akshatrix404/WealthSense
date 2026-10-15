package com.wealthsense.app.benchmark

import android.os.Debug
import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Test
import org.junit.runner.RunWith
import org.tensorflow.lite.Interpreter
import java.io.File
import java.nio.MappedByteBuffer
import java.nio.channels.FileChannel

/**
 * Measures forecaster inference latency and native-heap growth on a physical phone.
 * Run:  ./gradlew :app:connectedDebugAndroidTest --tests "*OnDeviceInferenceBenchmark*"
 * Read: adb logcat -s WS_BENCH
 *
 * Energy is not measured here. Measure it separately with a Perfetto/Battery Historian
 * trace around a loop of N inferences, or with the Android Studio Energy Profiler.
 * Report: device model, SoC, Android version, thermal state, charge state, N, and the
 * median / p95 latency printed below. Run each configuration at least 5 times.
 */
@RunWith(AndroidJUnit4::class)
class OnDeviceInferenceBenchmark {
    private fun load(name: String): MappedByteBuffer {
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        val fd = ctx.assets.openFd(name)
        return fd.createInputStream().channel.map(FileChannel.MapMode.READ_ONLY, fd.startOffset, fd.declaredLength)
    }

    @Test
    fun forecasterLatency() {
        val before = Debug.getNativeHeapAllocatedSize()
        val interp = Interpreter(load("forecaster.tflite"), Interpreter.Options().setNumThreads(2))
        val input = Array(1) { Array(30) { FloatArray(2) } }
        val output = Array(1) { FloatArray(1) }
        repeat(50) { interp.run(input, output) } // warm-up
        val n = 500
        val t = LongArray(n)
        for (i in 0 until n) { val s = System.nanoTime(); interp.run(input, output); t[i] = System.nanoTime() - s }
        t.sort()
        val after = Debug.getNativeHeapAllocatedSize()
        Log.i("WS_BENCH", "forecaster median_us=${t[n / 2] / 1000} p95_us=${t[(n * 95) / 100] / 1000} native_heap_delta_kb=${(after - before) / 1024}")
        interp.close()
    }
}
