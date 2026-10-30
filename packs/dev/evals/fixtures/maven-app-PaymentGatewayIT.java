package internal.example.noc;

import static org.junit.Assert.assertTrue;

import java.net.HttpURLConnection;
import java.net.URL;
import org.junit.Test;

/** Integration test against the partner sandbox. Needs network access to payments-sandbox.example.test. */
public class PaymentGatewayIT {
    @Test
    public void sandboxAnswers() throws Exception {
        URL url = new URL("https://payments-sandbox.example.test/health");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setConnectTimeout(5000);
        assertTrue(conn.getResponseCode() == 200);
    }
}
