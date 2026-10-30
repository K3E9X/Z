package internal.example.noc;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class AlarmFormatterTest {
    @Test
    public void formatsLinkDownAlarm() {
        String line = AlarmFormatter.format("core-par-02", "Te0/0/1", "LINK_DOWN");
        assertEquals("[core-par-02] Te0/0/1 LINK_DOWN", line);
    }
}
