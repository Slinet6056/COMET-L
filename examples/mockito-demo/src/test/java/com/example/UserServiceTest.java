package com.example;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.RepeatedTest;
import org.junit.jupiter.api.Timeout;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.Arguments;
import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.InjectMocks;
import org.mockito.Spy;
import org.mockito.MockitoAnnotations;
import org.mockito.stubbing.Answer;
import org.mockito.junit.jupiter.MockitoExtension;

public class UserServiceTest {

    @Test
    void testRegisterUserSucceedsWithValidInput() {
        UserRepository repository = mock(UserRepository.class);
        EmailService emailService = mock(EmailService.class);
        ValidationService validator = mock(ValidationService.class);
        UserService service = new UserService(repository, emailService, validator);
    
        when(validator.isValidUsername("john")).thenReturn(true);
        when(validator.isValidEmail("john@example.com")).thenReturn(true);
        when(repository.existsByUsername("john")).thenReturn(false);
        User savedUser = new User("john", "john@example.com");
        when(repository.save(any(User.class))).thenReturn(savedUser);
    
        User result = service.registerUser("john", "john@example.com");
    
        assertEquals(savedUser, result);
        verify(repository).existsByUsername("john");
        verify(repository).save(any(User.class));
        verify(emailService).sendWelcomeEmail("john@example.com");
    }

    @Test
    void testRegisterUserThrowsForInvalidUsername() {
        UserRepository repository = mock(UserRepository.class);
        EmailService emailService = mock(EmailService.class);
        ValidationService validator = mock(ValidationService.class);
        UserService service = new UserService(repository, emailService, validator);
    
        when(validator.isValidUsername("bad")).thenReturn(false);
    
        IllegalArgumentException exception = assertThrows(IllegalArgumentException.class, () -> service.registerUser("bad", "valid@example.com"));
        assertEquals("Invalid username", exception.getMessage());
        verify(repository, never()).existsByUsername(any());
        verify(repository, never()).save(any());
        verify(emailService, never()).sendWelcomeEmail(any());
    }

    @Test
    void testRegisterUserThrowsForInvalidEmail() {
        UserRepository repository = mock(UserRepository.class);
        EmailService emailService = mock(EmailService.class);
        ValidationService validator = mock(ValidationService.class);
        UserService service = new UserService(repository, emailService, validator);
    
        when(validator.isValidUsername("mike")).thenReturn(true);
        when(validator.isValidEmail("bad-email")).thenReturn(false);
    
        IllegalArgumentException exception = assertThrows(IllegalArgumentException.class, () -> service.registerUser("mike", "bad-email"));
        assertEquals("Invalid email", exception.getMessage());
        verify(repository, never()).existsByUsername(any());
        verify(repository, never()).save(any());
        verify(emailService, never()).sendWelcomeEmail(any());
    }

    @Test
    void testRegisterUserThrowsWhenUsernameAlreadyExists() {
        UserRepository repository = mock(UserRepository.class);
        EmailService emailService = mock(EmailService.class);
        ValidationService validator = mock(ValidationService.class);
        UserService service = new UserService(repository, emailService, validator);
    
        when(validator.isValidUsername("jane")).thenReturn(true);
        when(validator.isValidEmail("jane@example.com")).thenReturn(true);
        when(repository.existsByUsername("jane")).thenReturn(true);
    
        IllegalStateException exception = assertThrows(IllegalStateException.class, () -> service.registerUser("jane", "jane@example.com"));
        assertEquals("Username already exists", exception.getMessage());
        verify(repository).existsByUsername("jane");
        verify(repository, never()).save(any());
        verify(emailService, never()).sendWelcomeEmail(any());
    }

    @Test
    void testRegisterUserAllowsEmptyUsernameWhenValidatorAccepts() {
        UserRepository repository = mock(UserRepository.class);
        EmailService emailService = mock(EmailService.class);
        ValidationService validator = mock(ValidationService.class);
        UserService service = new UserService(repository, emailService, validator);
    
        when(validator.isValidUsername("" )).thenReturn(true);
        when(validator.isValidEmail("empty@example.com")).thenReturn(true);
        when(repository.existsByUsername("" )).thenReturn(false);
        when(repository.save(any(User.class))).thenAnswer(invocation -> invocation.getArgument(0));
    
        User result = service.registerUser("", "empty@example.com");
    
        assertEquals("", result.getUsername());
        assertEquals("empty@example.com", result.getEmail());
    
        ArgumentCaptor<User> userCaptor = ArgumentCaptor.forClass(User.class);
        verify(repository).save(userCaptor.capture());
        assertEquals("", userCaptor.getValue().getUsername());
        assertEquals("empty@example.com", userCaptor.getValue().getEmail());
        verify(emailService).sendWelcomeEmail("empty@example.com");
    }

    @Test
    void testFindUserByUsernameReturnsExistingUser() {
        UserRepository repository = mock(UserRepository.class);
        EmailService emailService = mock(EmailService.class);
        ValidationService validator = mock(ValidationService.class);
        UserService service = new UserService(repository, emailService, validator);
        User expectedUser = new User("alice", "alice@example.com");
    
        when(repository.findByUsername("alice")).thenReturn(expectedUser);
    
        User result = service.findUserByUsername("alice");
    
        assertSame(expectedUser, result);
        verify(repository).findByUsername("alice");
        verifyNoInteractions(emailService, validator);
    }

        @Test
    void testFindUserByUsernameReturnsNullWhenNotFound() {
        UserRepository repository = mock(UserRepository.class);
        EmailService emailService = mock(EmailService.class);
        ValidationService validator = mock(ValidationService.class);
        UserService service = new UserService(repository, emailService, validator);
    
        when(repository.findByUsername("missing")).thenReturn(null);
    
        User result = service.findUserByUsername("missing");
    
        assertEquals(null, result);
        verify(repository).findByUsername("missing");
        verifyNoInteractions(emailService, validator);
    }

        @Test
    void testFindUserByUsernamePropagatesNullUsernameToRepository() {
        UserRepository repository = mock(UserRepository.class);
        EmailService emailService = mock(EmailService.class);
        ValidationService validator = mock(ValidationService.class);
        UserService service = new UserService(repository, emailService, validator);
    
        when(repository.findByUsername(null)).thenReturn(null);
    
        User result = service.findUserByUsername(null);
    
        assertEquals(null, result);
        verify(repository).findByUsername(null);
        verifyNoInteractions(emailService, validator);
    }

        @Test
    void testFindUserByUsernameDelegatesToRepositoryWhenUsernameIsNull() {
        UserRepository repository = mock(UserRepository.class);
        EmailService emailService = mock(EmailService.class);
        ValidationService validator = mock(ValidationService.class);
        UserService service = new UserService(repository, emailService, validator);
    
        when(repository.findByUsername(null)).thenReturn(null);
    
        User result = service.findUserByUsername(null);
    
        assertNull(result);
        verify(repository).findByUsername(null);
        verifyNoInteractions(emailService, validator);
    }

        @Test
    void testFindUserByUsernameReturnsNullWhenUserMissing() {
        UserRepository repository = mock(UserRepository.class);
        EmailService emailService = mock(EmailService.class);
        ValidationService validator = mock(ValidationService.class);
        UserService service = new UserService(repository, emailService, validator);
    
        when(repository.findByUsername("missing")).thenReturn(null);
    
        User result = service.findUserByUsername("missing");
    
        assertNull(result);
        verify(repository).findByUsername("missing");
        verifyNoInteractions(emailService, validator);
    }

        @Test
    void testFindUserByUsernameDoesNotTrimInput() {
        UserRepository repository = mock(UserRepository.class);
        EmailService emailService = mock(EmailService.class);
        ValidationService validator = mock(ValidationService.class);
        UserService service = new UserService(repository, emailService, validator);
        String paddedUsername = "  bob  ";
        User expectedUser = new User(paddedUsername, "bob@example.com");
    
        when(repository.findByUsername(paddedUsername)).thenReturn(expectedUser);
    
        User result = service.findUserByUsername(paddedUsername);
    
        assertSame(expectedUser, result);
        verify(repository).findByUsername(paddedUsername);
        verifyNoInteractions(emailService, validator);
    }

        @Test
    void testUpdateUserEmailUpdatesEmailAndSendsNotification() {
        UserRepository repository = mock(UserRepository.class);
        EmailService emailService = mock(EmailService.class);
        ValidationService validator = mock(ValidationService.class);
        User user = new User("alice", "old@example.com");
        when(validator.isValidEmail("new@example.com")).thenReturn(true);
        when(repository.findByUsername("alice")).thenReturn(user);
    
        UserService service = new UserService(repository, emailService, validator);
        service.updateUserEmail("alice", "new@example.com");
    
        assertEquals("new@example.com", user.getEmail());
        verify(repository).findByUsername("alice");
        verify(repository).save(user);
        verify(emailService).sendEmailChangeNotification("new@example.com");
    }

        @Test
    void testUpdateUserEmailThrowsWhenEmailInvalid() {
        UserRepository repository = mock(UserRepository.class);
        EmailService emailService = mock(EmailService.class);
        ValidationService validator = mock(ValidationService.class);
        when(validator.isValidEmail("invalid-email")).thenReturn(false);
    
        UserService service = new UserService(repository, emailService, validator);
        IllegalArgumentException exception = assertThrows(IllegalArgumentException.class, () ->
                service.updateUserEmail("alice", "invalid-email"));
    
        assertEquals("Invalid email", exception.getMessage());
        verify(validator).isValidEmail("invalid-email");
        verifyNoInteractions(repository, emailService);
    }

        @Test
    void testUpdateUserEmailThrowsWhenUserNotFound() {
        UserRepository repository = mock(UserRepository.class);
        EmailService emailService = mock(EmailService.class);
        ValidationService validator = mock(ValidationService.class);
        when(validator.isValidEmail("new@example.com")).thenReturn(true);
        when(repository.findByUsername("missing")).thenReturn(null);
    
        UserService service = new UserService(repository, emailService, validator);
        IllegalArgumentException exception = assertThrows(IllegalArgumentException.class, () ->
                service.updateUserEmail("missing", "new@example.com"));
    
        assertEquals("User not found", exception.getMessage());
        verify(repository).findByUsername("missing");
        verify(repository, never()).save(any());
        verify(emailService, never()).sendEmailChangeNotification(anyString());
    }

        @Test
    void testUpdateUserEmailAllowsEmptyEmailWhenValidatorAccepts() {
        UserRepository repository = mock(UserRepository.class);
        EmailService emailService = mock(EmailService.class);
        ValidationService validator = mock(ValidationService.class);
        User user = new User("bob", "old@example.com");
        when(validator.isValidEmail("" )).thenReturn(true);
        when(repository.findByUsername("bob")).thenReturn(user);
    
        UserService service = new UserService(repository, emailService, validator);
        service.updateUserEmail("bob", "");
    
        assertEquals("", user.getEmail());
        verify(repository).save(user);
        verify(emailService).sendEmailChangeNotification("");
    }

        @Test
    void testUpdateUserEmailHandlesNullUsernameWhenRepositoryReturnsUser() {
        UserRepository repository = mock(UserRepository.class);
        EmailService emailService = mock(EmailService.class);
        ValidationService validator = mock(ValidationService.class);
        User user = new User(null, "old@example.com");
        when(validator.isValidEmail("new@example.com")).thenReturn(true);
        when(repository.findByUsername(null)).thenReturn(user);
    
        UserService service = new UserService(repository, emailService, validator);
        service.updateUserEmail(null, "new@example.com");
    
        assertEquals("new@example.com", user.getEmail());
        verify(repository).findByUsername(null);
        verify(repository).save(user);
        verify(emailService).sendEmailChangeNotification("new@example.com");
    }
}