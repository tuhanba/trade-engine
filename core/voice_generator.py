"""
core/voice_generator.py - Phase G Friday AI Text-To-Speech Module
==================================================================
Uses edge-tts to generate natural Turkish neural voices for Friday briefs.
"""

import asyncio
import logging
import os

logger = logging.getLogger("ax.voice_generator")


async def _generate_tts_async(text: str, output_path: str, voice: str = "tr-TR-DilaraNeural") -> None:
    import edge_tts
    # Clean text: remove complex markdown symbols that TTS might try to read literally
    clean_text = text.replace("**", "").replace("###", "").replace("##", "").replace("*", "").replace("`", "").replace("━━━━━━━━━━━━━━━━", "")
    
    communicate = edge_tts.Communicate(clean_text, voice)
    await communicate.save(output_path)


def generate_voice_briefing(text: str, output_path: str, voice: str = "tr-TR-DilaraNeural") -> bool:
    """
    Synchronous wrapper to generate neural Turkish voice file (.ogg / .mp3) from text.
    """
    try:
        # Create output directory if not exists
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            
        # Housekeeping: prune old voice files older than 24 hours
        if out_dir and os.path.exists(out_dir):
            import time
            now = time.time()
            cutoff = now - (24 * 3600)
            for filename in os.listdir(out_dir):
                file_path = os.path.join(out_dir, filename)
                if os.path.isfile(file_path):
                    try:
                        mtime = os.path.getmtime(file_path)
                        if mtime < cutoff:
                            os.remove(file_path)
                            logger.info(f"Housekeeping: pruned old voice file {filename}")
                    except Exception as he:
                        logger.warning(f"Housekeeping error for file {filename}: {he}")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_generate_tts_async(text, output_path, voice))
        loop.close()
        logger.info(f"Friday TTS generated successfully at: {output_path}")
        return True
    except Exception as e:
        logger.error(f"Friday TTS generation failed: {e}")
        return False
